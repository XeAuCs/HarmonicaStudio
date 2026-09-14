"""Per-song automatic projects using real files and silent, controlled workers."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from harmonica_studio.controller import AppController
from harmonica_studio.diagnostics import FakeAudio
from harmonica_studio.midi import write_midi
from harmonica_studio.project import load_project
from harmonica_studio.remote_control import RemoteControl, song_id
from harmonica_studio.song_projects import find_song_project
from workflow_fakes import ControlledExecutor, SilentScriptPlayer, finish_saves


class SongProjectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='harmonica-song-project-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'song.mid'
        write_midi([dict(pitch=60 + i, start=i * .5, end=i * .5 + .4, velocity=80)
                    for i in range(4)], self.source)
        self.controllers = []
        self.addCleanup(self.close_all)
        self.c, self.work, self.saves = self.controller()

    def controller(self):
        work, saves = ControlledExecutor(), ControlledExecutor()
        c = AppController(self.root / 'data', audio=FakeAudio(), player=SilentScriptPlayer(),
                          executor=work, save_executor=saves)
        self.controllers.append((c, saves))
        return c, work, saves

    def close_all(self):
        for c, saves in self.controllers:
            if not c.state.closed:
                c.close(wait=False)
                finish_saves(c, saves)

    def finish(self):
        finish_saves(self.c, self.saves)
        self.work.finish_next()
        self.c.poll()
        finish_saves(self.c, self.saves)

    def prepare(self):
        self.c.load_file(self.source, prepare=True)
        self.finish()
        self.finish()

    def test_marker_and_edits_survive_restart_and_midi_selection(self):
        self.prepare()
        c = self.c
        original_midi = self.source.read_bytes()
        notes = deepcopy(c.state.project['notes'])
        notes[0]['pitch'] = 70
        c.replace_notes(notes)
        c.set_highlight(.6)  # Queues its own save without a desktop listener.
        finish_saves(c, self.saves)
        path = c.state.auto_project_path
        self.assertEqual(path.parent, c.home / 'song-projects')
        self.assertEqual(load_project(path)['highlight'], .6)
        self.assertFalse(c.state.project_dirty)
        c.close(wait=False)
        finish_saves(c, self.saves)
        other, work, saves = self.controller()
        other.load_file(self.source)
        work.finish_next()
        other.poll()
        self.assertEqual(other.state.project['notes'], notes)
        self.assertEqual(other.state.project['highlight'], .6)
        self.assertEqual(other.state.project_path, path)
        self.assertIsNone(other.jobs.current)  # Opening does not re-extract the score.
        self.assertEqual(self.source.read_bytes(), original_midi)

    def test_clear_marker_is_saved_and_restored_as_absent(self):
        self.prepare()
        self.c.set_highlight(.6)
        self.c.set_highlight(None)
        self.c.load_file(self.source)
        self.finish()
        self.assertNotIn('highlight', self.c.state.project)
        self.assertNotIn('highlight', load_project(self.c.state.auto_project_path))

    def test_replaced_midi_uses_new_project_without_overwriting_old(self):
        self.prepare()
        self.c.set_highlight(.6)
        finish_saves(self.c, self.saves)
        old_path = self.c.state.auto_project_path
        write_midi([dict(pitch=72, start=0, end=1, velocity=80)], self.source)
        self.c.load_file(self.source, prepare=True)
        self.finish()
        self.assertIsNone(self.c.state.project)
        self.finish()
        self.assertNotEqual(self.c.state.auto_project_path, old_path)
        self.assertNotIn('highlight', self.c.state.project)
        self.assertEqual(load_project(old_path)['highlight'], .6)

    def test_same_named_files_in_different_folders_are_independent(self):
        self.prepare()
        other = self.root / 'other' / self.source.name
        other.parent.mkdir()
        other.write_bytes(self.source.read_bytes())
        path, project = find_song_project(self.c.home / 'song-projects', other)
        self.assertNotEqual(path, self.c.state.auto_project_path)
        self.assertIsNone(project)

    def test_stop_during_cached_load_prevents_autoplay(self):
        self.prepare()
        self.c.set_highlight(.6)
        finish_saves(self.c, self.saves)
        self.c.load_file(self.source, prepare=True, autoplay=True)
        self.c.stop_preview()
        self.finish()
        self.assertEqual(self.c.jobs.current.kind, 'export')
        self.finish()
        self.assertFalse(self.c.audio.playing)
        self.assertEqual(self.c.state.project['highlight'], .6)

    def test_remote_selection_restores_engine_and_plays_from_marker(self):
        self.prepare()
        self.c.set_highlight(.6)
        finish_saves(self.c, self.saves)
        self.c.update_preferences(replace(self.c.preferences, start_from_highlight=True))
        self.c.library = [dict(path=self.source, title='song')]
        remote = RemoteControl(self.c)
        reply = remote.handle(dict(action='select', song_id=song_id(self.source), autoplay=True))
        self.assertTrue(reply['ok'], reply)
        self.finish()
        self.assertEqual(self.c.jobs.current.kind, 'export')
        self.finish()
        self.assertTrue(self.c.audio.playing)
        self.assertAlmostEqual(self.c.audio.position, self.c.to_audio(.6))

    def test_stop_during_cached_export_and_stale_load_do_not_autoplay(self):
        self.prepare()
        self.c.load_file(self.source, prepare=True, autoplay=True)
        self.finish()
        self.c.stop_preview()
        self.finish()
        self.assertFalse(self.c.audio.playing)
        self.c.load_file(self.source, prepare=True, autoplay=True)
        self.c.state.revision += 1
        self.finish()
        self.assertIsNone(self.c.state.project)
        self.assertIsNone(self.c.jobs.current)
        self.assertFalse(self.c.audio.playing)

    def test_failed_save_blocks_switch_and_keeps_old_project(self):
        self.prepare()
        path = self.c.state.auto_project_path
        original = path.read_bytes()
        self.c.set_highlight(.6)
        self.c.load_file(self.source)
        with patch('harmonica_studio.save_coordinator.save_project', side_effect=OSError('disk full')):
            with self.assertLogs(level='ERROR'):
                finish_saves(self.c, self.saves)
        self.assertIsNone(self.c.state.transition)
        self.assertIsNone(self.c.jobs.current)
        self.assertEqual(self.c.state.project['highlight'], .6)
        self.assertTrue(self.c.state.project_dirty)
        self.assertEqual(path.read_bytes(), original)
        self.c.autosave()
        finish_saves(self.c, self.saves)
        self.assertEqual(load_project(path)['highlight'], .6)

    def test_corrupt_cache_is_reported_and_never_overwritten(self):
        self.prepare()
        path = self.c.state.auto_project_path
        path.write_text('{broken', encoding='utf-8')
        self.c.load_file(self.source, prepare=True)
        with self.assertLogs(level='ERROR'):
            self.finish()
        self.assertIsNone(self.c.state.project)
        self.assertIsNone(self.c.jobs.current)
        self.assertEqual(path.read_text(encoding='utf-8'), '{broken')
        self.assertIn('无法打开自动工程', self.c.state.message)

    def test_opened_engine_marker_saves_back_without_manual_save(self):
        self.prepare()
        path = self.c.state.auto_project_path
        self.c.open_project(path)
        self.c.set_highlight(.6)
        finish_saves(self.c, self.saves)
        self.assertEqual(load_project(path)['highlight'], .6)

    def test_fingerprinting_honors_cancel(self):
        cancel = Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            find_song_project(self.c.home / 'song-projects', self.source, cancel)
