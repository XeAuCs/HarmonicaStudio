"""Library concurrency contracts without timers, devices or personal data."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
from threading import Event, get_ident
import unittest
from unittest.mock import patch

from harmonica_studio.controller import AppController
from harmonica_studio.diagnostic_backends import ControlledExecutor, SilentScriptPlayer
from harmonica_studio.diagnostics import FakeAudio
from harmonica_studio.metrics import MetricsRecorder
from harmonica_studio.midi import write_midi
from harmonica_studio.project import make_project, save_project
from harmonica_studio.remote_control import RemoteControl
from harmonica_studio.library import sample_entries


class LibraryCoordinatorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='harmonica-library-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executor, self.music_jobs = ControlledExecutor(), ControlledExecutor()
        self.metrics = MetricsRecorder()
        self.c = AppController(self.root / 'data', executor=self.music_jobs,
                               library_executor=self.executor, metrics=self.metrics,
                               audio=FakeAudio(), player=SilentScriptPlayer())
        self.addCleanup(self.c.close)
        self.first, self.second = self.root / 'first', self.root / 'second'
        for folder in (self.first, self.second):
            folder.mkdir()
            write_midi([dict(pitch=60, start=0, end=.1, velocity=80)], folder / (folder.name + '.mid'))
        self.c.update_preferences(replace(self.c.preferences, library_folder=str(self.first)))
        self.finish()

    def finish(self):
        self.executor.finish_next()
        self.c.poll()

    def test_scan_uses_worker_and_installs_only_when_owner_polls(self):
        c = self.c
        owner, workers, events = get_ident(), [], []
        c.subscribe(lambda event, value: events.append((event, get_ident())))
        (self.first / 'new.mid').write_bytes(b'midi')
        requested = c.refresh_library()
        state, entries = deepcopy(c.state), deepcopy(c.library)
        events.clear()

        def scan(*args, **kwargs):
            workers.append(get_ident())
            return sample_entries(*args, **kwargs)

        with patch('harmonica_studio.library_coordinator.sample_entries', side_effect=scan), \
                ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.executor.finish_next).result(timeout=5)
        self.assertEqual(c.state, state)
        self.assertEqual(c.library, entries)
        self.assertEqual(events, [])
        c.poll()
        self.assertEqual(c.state.library_revision, requested)
        self.assertEqual(len(c.library), 2)
        self.assertTrue(workers and all(thread != owner for thread in workers))
        self.assertTrue(events and all(thread == owner for _, thread in events))

    def test_repeated_requests_keep_one_followup_and_reject_old_scan(self):
        c = self.c
        c.refresh_library()
        self.executor.finish_next()  # Snapshot predates the following file addition.
        (self.first / 'new.mid').write_bytes(b'midi')
        for _ in range(30):
            latest = c.refresh_library()
        c.poll()
        self.assertEqual(len(self.executor.pending), 1)
        self.assertEqual(len(c.library), 1)
        self.finish()
        self.assertEqual(len(c.library), 2)
        self.assertEqual(c.state.library_revision, latest)
        self.assertFalse(c.library_refreshing)
        self.assertEqual(self.executor.pending, [])

    def test_switching_directory_rejects_old_result_even_after_worker_finished(self):
        c = self.c
        old = deepcopy(c.library)
        c.refresh_library()
        self.executor.finish_next()
        c.update_preferences(replace(c.preferences, library_folder=str(self.second)))
        latest = c.library_scanner.latest.generation
        self.assertTrue(c.library_scanner.jobs.current.cancel.is_set())
        c.poll()
        self.assertEqual(c.library, old)
        self.finish()
        self.assertEqual(c.state.library_revision, latest)
        self.assertEqual(c.state.library_root, self.second)
        self.assertEqual([e['file'] for e in c.library], ['second.mid'])

    def test_stale_failure_is_ignored_and_latest_scan_still_runs(self):
        c = self.c
        c.refresh_library()
        with patch('harmonica_studio.library_coordinator.sample_entries', side_effect=PermissionError('old')):
            self.executor.finish_next()
        c.update_preferences(replace(c.preferences, library_folder=str(self.second)))
        events = []
        c.subscribe(lambda event, value: events.append(event))
        c.poll()
        self.finish()
        self.assertNotIn('error', events)
        self.assertEqual(c.state.library_root, self.second)

    def test_failure_preserves_entries_and_remote_refresh_can_retry(self):
        c = self.c
        remote = RemoteControl(c)
        old, revision = deepcopy(c.library), c.state.library_revision
        events = []
        c.subscribe(lambda event, value: events.append((event, value)))
        reply = remote.handle({'action': 'refresh'})
        self.assertTrue(reply['ok'])
        self.assertIn('正在刷新', reply['message'])
        snapshot = remote.snapshot()
        self.assertTrue(snapshot['library_refreshing'])
        self.assertFalse(snapshot['busy'])
        with patch('harmonica_studio.library_coordinator.sample_entries', side_effect=PermissionError('denied')), \
                self.assertLogs(level='ERROR'):
            self.finish()
        self.assertEqual(c.library, old)
        self.assertEqual(c.state.library_revision, revision)
        self.assertEqual(c.state.library_error, 'denied')
        self.assertTrue([value for event, value in events if event == 'error'][0][1])
        self.assertFalse(c.library_refreshing)
        remote.handle({'action': 'refresh'})
        self.finish()
        self.assertEqual(c.state.library_error, '')
        self.assertGreater(c.state.library_revision, revision)

    def test_scan_does_not_block_edit_export_or_playback(self):
        c = self.c
        path = self.root / 'score.hstudio'
        save_project(path, make_project([dict(pitch=60, start=0, end=.1)]))
        c.open_project(path)
        c.refresh_library()
        self.assertFalse(c.busy)
        self.assertTrue(c.capabilities()['can_edit'])
        c.replace_notes([dict(pitch=62, start=0, end=.1)])
        c.listen()
        self.music_jobs.finish_next()
        c.poll()
        self.assertTrue(c.audio.playing)
        self.assertTrue(c.library_refreshing)
        revision = c.state.revision
        self.finish()
        self.assertTrue(c.audio.playing)
        self.assertEqual(c.state.revision, revision)

    def test_close_cancels_running_and_pending_scans_and_ignores_late_result(self):
        c = self.c
        c.refresh_library()
        future, _, _, _ = self.executor.pending.pop()
        future.set_running_or_notify_cancel()
        c.refresh_library()
        cancel = c.library_scanner.jobs.current.cancel
        events = []
        c.subscribe(lambda event, value: events.append(event))
        c.close()
        events.clear()
        future.set_result([])
        c.poll()
        self.assertTrue(cancel.is_set())
        self.assertFalse(c.library_refreshing)
        self.assertEqual(events, [])
        self.assertIsNone(c.library_scanner.pending)
        with self.assertRaises(RuntimeError):
            c.refresh_library()

    def test_failed_shutdown_save_leaves_scan_available(self):
        c = self.c
        c.refresh_library()
        c.state.set_project(make_project([dict(pitch=60, start=0, end=.1)]), new_document=True)
        with patch('harmonica_studio.save_coordinator.save_project', side_effect=OSError('disk')), self.assertLogs(level='ERROR'):
            with self.assertRaises(OSError):
                c.close()
        self.assertFalse(c.library_scanner.jobs.closed)
        self.finish()
        self.assertFalse(c.state.closed)

    def test_real_scan_observes_shutdown_cancellation(self):
        # The actual worker remains independent while the controller is closed.
        started, cancelled = Event(), Event()

        def scan(root, cancel):
            started.set()
            if not cancel.wait(5):
                raise AssertionError('scan cancellation was not signalled')
            cancelled.set()
            raise InterruptedError()

        pool = ThreadPoolExecutor(max_workers=1)
        c = AppController(self.root / 'real', library_executor=pool,
                          audio=FakeAudio(), player=SilentScriptPlayer())
        try:
            with patch('harmonica_studio.library_coordinator.scan_library', side_effect=scan):
                c.refresh_library()
                self.assertTrue(started.wait(2))
                c.close()
                self.assertTrue(cancelled.wait(2))
        finally:
            c.close()
            pool.shutdown(wait=True, cancel_futures=True)
