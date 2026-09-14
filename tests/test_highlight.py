"""Saved score markers and both playback paths, with no real input injection."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import subprocess
import tempfile
import unittest

from harmonica_studio.app_state import Transport
from harmonica_studio.controller import AppController
from harmonica_studio.diagnostic_backends import ControlledExecutor, SilentScriptPlayer
from harmonica_studio.diagnostics import FakeAudio
from harmonica_studio.paths import resource_root, template_path
from harmonica_studio.preferences import Preferences, load_preferences, save_preferences
from harmonica_studio.preview import decode_events
from harmonica_studio.project import make_project, validate_project, save_project, load_project
from harmonica_studio.remote_control import RemoteControl
from harmonica_studio.schedule import build_events
from workflow_fakes import finish_saves


class HighlightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='harmonica-highlight-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executor, self.saves = ControlledExecutor(), ControlledExecutor()
        self.audio, self.player = FakeAudio(), SilentScriptPlayer()
        self.c = AppController(self.root / 'data', audio=self.audio, player=self.player,
                               executor=self.executor, save_executor=self.saves)
        self.addCleanup(self.close)
        self.project = make_project([dict(pitch=60, start=0, end=1, velocity=80),
                                     dict(pitch=73, start=10, end=11, velocity=90)])
        self.path = self.root / 'song.hstudio'
        save_project(self.path, self.project)
        self.c.open_project(self.path)

    def close(self):
        self.c.close(wait=False)
        finish_saves(self.c, self.saves)

    def finish(self):
        self.executor.finish_next()
        self.c.poll()
        finish_saves(self.c, self.saves)

    def enable(self):
        self.c.update_preferences(replace(self.c.preferences, start_from_highlight=True))

    def test_marker_roundtrip_export_and_old_files_remain_compatible(self):
        self.assertNotIn('highlight', load_project(self.path))
        self.c.set_highlight(10)
        self.assertTrue(self.c.state.project_dirty)
        self.c.save_to(self.path)
        finish_saves(self.c, self.saves)
        self.assertEqual(load_project(self.path)['highlight'], 10)
        self.c.open_project(self.path)
        self.c.begin_export()
        self.finish()
        exported = load_project(self.c.state.result[0] / '工程.hstudio')
        self.assertEqual(exported['highlight'], 10)
        self.assertEqual(exported['notes'], self.project['notes'])
        self.assertFalse(self.c.state.project_dirty)

    def test_invalid_markers_do_not_change_state_or_existing_save(self):
        original = deepcopy(self.c.state)
        for value in (-1, 11, 99, True, '2', float('nan'), float('inf'), 10**500):
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                self.c.set_highlight(value)
            self.assertEqual(self.c.state, original)
        for value in (0, 10.999):
            self.assertEqual(validate_project(dict(self.project, highlight=value))['highlight'], value)
        with self.assertRaises(ValueError):
            validate_project(dict(make_project([]), highlight=0))

    def test_preview_uses_mapped_marker_but_pause_and_explicit_seek_take_priority(self):
        c = self.c
        c.set_highlight(10)
        self.enable()
        c.listen()
        self.finish()
        self.assertAlmostEqual(self.audio.position, c.to_audio(10))
        self.assertLess(self.audio.position, 2)  # Long silence was compressed.
        self.audio.advance(.2)
        c.pause()
        paused = self.audio.position
        c.listen()
        self.assertAlmostEqual(self.audio.position, paused)
        c.stop_preview()
        c.seek_score(.5)
        c.listen()
        self.assertAlmostEqual(self.audio.position, c.to_audio(.5))
        self.audio.advance(100)
        c.update_playback()
        self.assertEqual(c.state.transport, Transport.ENDED)
        c.listen()
        self.assertAlmostEqual(self.audio.position, c.to_audio(10))

    def test_disabled_or_missing_marker_preserves_normal_start(self):
        self.c.set_highlight(10)
        self.c.stop_preview()
        self.c.listen()
        self.finish()
        self.assertLess(self.audio.position, 1)
        self.c.stop_preview()
        self.c.set_highlight(None)
        self.enable()
        self.c.listen()
        self.finish()
        self.assertLess(self.audio.position, 1)

    def test_remote_game_and_arming_use_same_physical_start_as_preview(self):
        c = self.c
        c.set_highlight(10)
        self.enable()
        remote = RemoteControl(c)
        self.assertTrue(remote.handle({'action': 'game_play'})['ok'])
        self.finish()
        self.assertEqual(self.player.calls[-1][0], 'play')
        self.assertAlmostEqual(self.player.calls[-1][2], c.to_audio(10))
        c.stop_game(close=True)
        c.game_play(arm=True)
        self.assertEqual(self.player.calls[-1][0], 'arm')
        self.assertAlmostEqual(self.player.calls[-1][2], c.to_audio(10))
        self.assertEqual(remote.snapshot()['highlight'], 10)
        self.assertTrue(remote.handle({'action': 'play'})['ok'])
        self.assertAlmostEqual(self.audio.position, c.to_audio(10))
        c.stop_preview()
        c.update_preferences(replace(c.preferences, start_from_highlight=False))
        c.game_play()
        self.assertEqual(len(self.player.calls[-1]), 2)

    def test_marker_edits_are_blocked_during_play_or_job_and_stop_cancels_followup(self):
        c = self.c
        c.set_highlight(10)
        self.enable()
        c.listen()
        with self.assertRaises(RuntimeError):
            c.set_highlight(None)
        c.stop_preview()
        self.finish()
        self.assertFalse(self.audio.playing)
        c.listen()
        with self.assertRaises(RuntimeError):
            c.set_highlight(None)
        c.pause()
        c.set_highlight(None)
        self.assertNotIn('highlight', c.state.project)

    def test_note_edits_keep_in_range_marker_and_remove_marker_beyond_new_end(self):
        self.c.set_highlight(10)
        notes = deepcopy(self.project['notes'])
        notes[0]['pitch'] = 62
        self.c.replace_notes(notes)
        self.assertEqual(self.c.state.project['highlight'], 10)
        self.c.replace_notes(notes[:1])
        self.assertNotIn('highlight', self.c.state.project)
        self.c.set_highlight(.5)
        self.c.replace_notes([])
        self.assertNotIn('highlight', self.c.state.project)

    def test_start_preference_persists_without_invalidating_export(self):
        self.c.begin_export()
        self.finish()
        version = self.c.state.revision
        self.enable()
        self.assertEqual(self.c.state.revision, version)
        self.assertFalse(self.c.state.export_dirty)
        self.assertTrue(load_preferences(self.c.home / 'preferences.json').start_from_highlight)
        path = self.root / 'preferences.json'
        for raw in ({}, {'start_from_highlight': 'true'}, {'start_from_highlight': 1}):
            path.write_text(json.dumps(raw), encoding='utf-8')
            self.assertFalse(load_preferences(path).start_from_highlight)
        save_preferences(path, Preferences(start_from_highlight=True))
        self.assertTrue(load_preferences(path).start_from_highlight)

    def test_marker_just_before_end_does_not_round_to_end_for_game(self):
        self.c.set_highlight(10.99999)
        self.enable()
        self.c.game_play()
        self.finish()
        milliseconds=round(self.player.calls[-1][2]*1000)
        final_release=round(self.c.to_audio(self.c.state.score_duration)*1000)
        self.assertLess(milliseconds,final_release)


@unittest.skipUnless((resource_root() / 'third_party/AutoHotkey/AutoHotkey64.exe').is_file(),
                     'Requires bundled AHK for validation only; never sends input')
class HighlightEventTests(unittest.TestCase):
    def test_actual_ahk_slicing_keeps_pitches_timing_and_balanced_keys(self):
        notes = [dict(pitch=49, start=0, end=1), dict(pitch=73, start=4, end=5),
                 dict(pitch=60, start=5, end=6)]
        events, _ = build_events(notes)
        actual = decode_events(events)
        template = template_path().read_text(encoding='utf-8-sig')
        with tempfile.TemporaryDirectory(prefix='harmonica-highlight-ahk-') as tmp:
            script = Path(tmp) / 'validate.ahk'
            script.write_text(template.replace('__EVENTS__', '\n'.join(','.join(map(str, e)) for e in events)), encoding='utf-8-sig')
            for start_ms in (0, 50, 75, 100, 500, 1100, 2000, 4080, 4100, 4500, 5080, 6100):
                with self.subTest(start_ms=start_ms):
                    result = subprocess.run([str(resource_root() / 'third_party/AutoHotkey/AutoHotkey64.exe'),
                                             '/ErrorStdOut', str(script), '--dump-events', str(start_ms)],
                                            capture_output=True, timeout=10, encoding='utf-8-sig')
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    rows = [line.split(',') for line in result.stdout.splitlines() if line]
                    sliced = [[int(t), key, int(down)] for t, key, down in rows]
                    if start_ms == 0:
                        self.assertEqual(sliced, events)
                        continue
                    decoded = decode_events(sliced)
                    expected = [n for n in actual if round(n['end'] * 1000) > start_ms]
                    self.assertEqual([n['pitch'] for n in decoded], [n['pitch'] for n in expected])
                    for original, clipped in zip(expected, decoded):
                        self.assertAlmostEqual(clipped['start'], max(original['start'], start_ms / 1000) - start_ms / 1000 + .1)
                        self.assertAlmostEqual(clipped['end'], original['end'] - start_ms / 1000 + .1)


if __name__ == '__main__':
    unittest.main()
