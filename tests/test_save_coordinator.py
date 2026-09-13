"""Persistence ordering and transition safety using real files and controlled workers."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
from threading import get_ident
import unittest
from unittest.mock import patch

from harmonica_studio.controller import AppController
from harmonica_studio.diagnostics import FakeAudio
from harmonica_studio.midi import write_midi
from harmonica_studio.project import make_project, save_project, load_project
from harmonica_studio.remote_control import RemoteControl
from harmonica_studio.save_coordinator import SaveKind
from workflow_fakes import ControlledExecutor, SilentScriptPlayer, finish_saves


class SaveCoordinatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='harmonica-save-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executor, self.music = ControlledExecutor(), ControlledExecutor()
        self.c = AppController(self.root / 'data', audio=FakeAudio(), player=SilentScriptPlayer(),
                               executor=self.music, save_executor=self.executor)
        self.addCleanup(self.close_controller)
        self.path = self.root / 'input.hstudio'
        save_project(self.path, make_project([dict(pitch=60, start=0, end=.1)], report={'extra': {'value': 1}}))
        self.c.open_project(self.path)

    def close_controller(self):
        if not self.c.state.closed:
            self.c.close(wait=False)
            finish_saves(self.c, self.executor)
        self.assertTrue(self.c.state.closed)

    def edit(self, pitch):
        self.c.replace_notes([dict(pitch=pitch, start=0, end=.1)])

    def test_save_worker_owns_independent_snapshot_and_only_poll_confirms_version(self):
        c, workers, events = self.c, [], []
        self.edit(62)
        destination = self.root / 'saved.hstudio'
        request = c.save_to(destination)
        c.subscribe(lambda event, value: events.append((event, get_ident())))
        state = deepcopy(c.state)

        def write(path, project):
            workers.append(get_ident())
            return save_project(path, project)

        with patch('harmonica_studio.save_coordinator.save_project', side_effect=write), \
                ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.executor.finish_next).result(timeout=5)
        self.assertEqual(c.state, state)
        self.assertEqual(events, [])
        self.assertTrue(c.state.project_dirty)
        self.assertEqual(load_project(destination)['notes'][0]['pitch'], 62)
        c.poll()
        self.assertEqual(c.state.saved_revision, request.snapshot.revision)
        self.assertFalse(c.state.project_dirty)
        self.assertTrue(workers and all(thread != get_ident() for thread in workers))
        self.assertTrue(events and all(thread == get_ident() for _, thread in events))

    def test_edit_during_manual_save_remains_dirty_and_does_not_change_snapshot(self):
        c = self.c
        self.edit(62)
        destination = self.root / 'saved.hstudio'
        request = c.save_to(destination)
        self.assertTrue(c.capabilities()['can_edit'])
        self.edit(64)
        c.autosave()
        finish_saves(c, self.executor)
        self.assertEqual(load_project(destination)['notes'][0]['pitch'], 62)
        self.assertEqual(load_project(c.home / 'autosave.hstudio')['notes'][0]['pitch'], 64)
        self.assertEqual(c.state.saved_revision, request.snapshot.revision)
        self.assertTrue(c.state.project_dirty)

    def test_autosaves_coalesce_but_never_cross_manual_save(self):
        c, written = self.c, []
        c.autosave()
        self.edit(61)
        c.autosave()
        self.edit(62)
        c.autosave()
        c.save_to(self.root / 'manual.hstudio')
        self.edit(63)
        c.autosave()

        def write(path, project):
            written.append((Path(path).name, project['notes'][0]['pitch']))
            save_project(path, project)

        with patch('harmonica_studio.save_coordinator.save_project', side_effect=write):
            finish_saves(c, self.executor)
        self.assertEqual(written, [('autosave.hstudio', 60), ('autosave.hstudio', 62),
                                   ('manual.hstudio', 62), ('autosave.hstudio', 63)])

    def test_repeated_manual_saves_to_same_path_are_serial(self):
        c = self.c
        destination = self.root / 'manual.hstudio'
        self.edit(61)
        c.save_to(destination)
        self.edit(62)
        latest = c.save_to(destination)
        self.assertEqual(len(self.executor.pending), 1)
        finish_saves(c, self.executor)
        self.assertEqual(load_project(destination)['notes'][0]['pitch'], 62)
        self.assertEqual(c.state.saved_revision, latest.snapshot.revision)

    def test_old_document_receipt_cannot_mark_new_document_saved(self):
        c = self.c
        self.edit(62)
        c.save_to(self.root / 'old.hstudio')
        c.state.set_project(make_project([], 'new'), saved=True, new_document=True)
        c.state.project_path = self.root / 'new.hstudio'
        revision = c.state.saved_revision
        finish_saves(c, self.executor)
        self.assertEqual(c.state.saved_revision, revision)
        self.assertEqual(c.state.project_path, self.root / 'new.hstudio')
        self.assertEqual(c.state.autosave_revision, -1)

    def test_switch_waits_for_recovery_and_orders_new_document_autosave_last(self):
        c = self.c
        self.edit(62)
        c.autosave()
        other = self.root / 'other.hstudio'
        save_project(other, make_project([dict(pitch=65, start=0, end=.1)]))
        self.assertFalse(c.open_project(other))
        self.assertEqual(c.state.project['notes'][0]['pitch'], 62)
        self.assertFalse(c.capabilities()['can_edit'])
        finish_saves(c, self.executor)
        recovery = list((c.home / 'recovery').glob('*.hstudio'))
        self.assertEqual(len(recovery), 1)
        self.assertEqual(load_project(recovery[0])['notes'][0]['pitch'], 62)
        c.autosave()
        finish_saves(c, self.executor)
        self.assertEqual(load_project(c.home / 'autosave.hstudio')['notes'][0]['pitch'], 65)
        self.assertEqual(c.state.project_path, other)

    def test_failed_preservation_aborts_switch_and_keeps_existing_project_usable(self):
        c = self.c
        self.edit(62)
        other = self.root / 'other.hstudio'
        save_project(other, make_project([]))
        c.open_project(other)
        with patch('harmonica_studio.save_coordinator.save_project', side_effect=OSError('disk')), \
                self.assertLogs(level='ERROR'):
            finish_saves(c, self.executor)
        self.assertEqual(c.state.project_path, self.path)
        self.assertTrue(c.state.project_dirty)
        self.assertIsNone(c.state.transition)
        self.assertTrue(c.capabilities()['can_edit'])
        self.assertFalse(c.state.closed)
        c.open_project(other)
        finish_saves(c, self.executor)
        self.assertEqual(c.state.project_path, other)

    def test_stop_while_preserving_selection_clears_later_autoplay(self):
        c = self.c
        self.edit(62)
        midi = self.root / 'other.mid'
        write_midi([dict(pitch=65, start=0, end=.1, velocity=80)], midi)
        c.load_file(midi, prepare=True, autoplay=True, remote=True)
        self.assertEqual(c.state.transition, 'load')
        self.assertTrue(RemoteControl(c).handle({'action': 'stop'})['ok'])
        finish_saves(c, self.executor)
        for _ in range(2):
            self.music.finish_next()
            c.poll()
        finish_saves(c, self.executor)
        self.assertFalse(c.audio.playing)
        self.assertFalse(c.state.export_dirty)

    def test_close_waits_for_save_and_ignores_completed_export_until_protected(self):
        c = self.c
        self.edit(62)
        c.listen()
        self.music.finish_next()
        self.assertFalse(c.close(wait=False))
        self.assertFalse(c.state.closed)
        c.poll()
        self.assertIsNone(c.state.result)
        finish_saves(c, self.executor)
        self.assertTrue(c.state.closed)
        self.assertFalse(c.audio.playing)
        self.assertEqual(load_project(c.home / 'autosave.hstudio')['notes'][0]['pitch'], 62)

    def test_failed_manual_save_aborts_already_requested_close(self):
        c = self.c
        self.edit(62)
        c.save_to(self.root / 'manual.hstudio')
        c.close(wait=False)
        with patch('harmonica_studio.save_coordinator.save_project', side_effect=OSError('manual failed')):
            self.executor.finish_next()
        with self.assertLogs(level='ERROR'):
            c.poll()
        finish_saves(c, self.executor)
        self.assertFalse(c.state.closed)
        self.assertIsNone(c.state.transition)
        self.assertTrue(c.state.project_dirty)
        self.assertTrue(c.capabilities()['can_edit'])

    def test_save_runs_without_waiting_for_conversion(self):
        c = self.c
        self.edit(62)
        c.begin_export()
        c.autosave()
        finish_saves(c, self.executor)
        self.assertTrue(c.busy)
        self.assertEqual(load_project(c.home / 'autosave.hstudio')['notes'][0]['pitch'], 62)
        self.assertEqual(len(self.music.pending), 1)

    def test_queue_rejects_excess_requests_without_dropping_existing_saves(self):
        c = self.c
        snapshot = c._save_snapshot()
        paths = []
        for i in range(c.saves.MAX_PENDING + 1):
            path = self.root / f'saved-{i}.hstudio'
            paths.append(path)
            c.saves.submit(snapshot, (path,), SaveKind.MANUAL)
        with self.assertRaises(RuntimeError):
            c.saves.submit(snapshot, (self.root / 'overflow.hstudio',), SaveKind.MANUAL)
        finish_saves(c, self.executor)
        self.assertTrue(all(path.is_file() for path in paths))

    def test_snapshot_does_not_share_nested_metadata_with_active_document(self):
        c = self.c
        c.autosave()
        c.state.project['report']['extra']['value'] = 2
        finish_saves(c, self.executor)
        self.assertEqual(load_project(c.home / 'autosave.hstudio')['report']['extra']['value'], 1)

    def test_submission_failure_does_not_leave_transition_stuck(self):
        c = self.c
        self.edit(62)
        with patch.object(self.executor, 'submit', side_effect=RuntimeError('executor unavailable')):
            c.close(wait=False)
        with self.assertLogs(level='ERROR'):
            c.poll()
        self.assertFalse(c.saving)
        self.assertIsNone(c.state.transition)
        self.assertFalse(c.state.closed)
        self.assertTrue(c.capabilities()['can_edit'])
