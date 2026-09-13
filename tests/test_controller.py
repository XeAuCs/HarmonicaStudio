"""Headless application contracts. No QApplication, dialogs, real audio or AHK."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from harmonica_studio.app_state import FollowUp, Transport
from harmonica_studio.controller import AppController
from harmonica_studio.diagnostics import FakeAudio
from harmonica_studio.midi import write_midi
from harmonica_studio.project import load_project, make_project, save_project
from harmonica_studio.remote_control import RemoteControl
from workflow_fakes import ControlledExecutor, SilentScriptPlayer


class ControllerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='harmonica-controller-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.executor, self.audio, self.player = ControlledExecutor(), FakeAudio(), SilentScriptPlayer()
        self.controller = AppController(self.root / 'data', audio=self.audio,
                                        player=self.player, executor=self.executor)
        self.addCleanup(self.controller.close)
        self.remote = RemoteControl(self.controller)
        self.notes = [dict(pitch=60, start=0, end=.1, velocity=80),
                      dict(pitch=73, start=.2, end=.3, velocity=100)]
        self.path = self.root / 'score.hstudio'
        save_project(self.path, make_project(self.notes))
        self.controller.open_project(self.path)

    def finish(self):
        self.executor.finish_next()
        self.controller.poll()

    def test_imports_work_without_loading_qt(self):
        result = subprocess.run([sys.executable, '-c',
            "import sys; from harmonica_studio.controller import AppController; "
            "from harmonica_studio.remote_control import RemoteControl; "
            "assert not any(k.startswith('PySide6') for k in sys.modules)"],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_desktop_and_remote_share_transport_and_stop_pending_play(self):
        c = self.controller
        c.listen()
        self.assertEqual(c.jobs.current.follow_up, FollowUp.PLAY)
        self.assertTrue(self.remote.handle({'action': 'stop'})['ok'])
        self.finish()
        self.assertFalse(self.audio.playing)
        self.assertTrue(self.remote.handle({'action': 'play'})['ok'])
        self.assertEqual(c.state.transport, Transport.PLAYING)
        c.pause()
        self.assertEqual(self.remote.snapshot()['transport'], 'paused')
        self.remote.handle({'action': 'seek', 'position': .15})
        self.assertAlmostEqual(self.audio.position, .15)
        c.stop_preview()
        self.assertEqual(self.remote.snapshot()['position'], 0)

    def test_busy_commands_cannot_mutate_the_active_document(self):
        c = self.controller
        c.begin_export()
        original = deepcopy(c.state.project)
        for operation in (lambda: c.replace_notes([]), lambda: c.open_project(self.path),
                          lambda: c.begin_export(), lambda: c.save_to(self.path)):
            with self.assertRaises(RuntimeError):
                operation()
            self.assertEqual(c.state.project, original)
        self.assertFalse(self.remote.handle({'action': 'play'})['ok'])
        self.finish()
        self.assertFalse(c.busy)

    def test_seek_before_render_survives_audio_load_failure_and_retry(self):
        c = self.controller
        c.seek_score(.2)
        self.audio.fail_next_load = True
        c.listen()
        with self.assertLogs(level='ERROR'):
            self.finish()
        folder = c.state.result[0]
        self.assertFalse(c.state.export_dirty)
        c.listen()
        self.assertEqual(c.state.result[0], folder)
        self.assertAlmostEqual(self.audio.position, c.to_audio(.2))
        self.assertTrue(self.audio.playing)

    def test_cancel_after_worker_finishes_does_not_install_or_play_result(self):
        c = self.controller
        c.listen()
        self.executor.finish_next()  # Result exists, but the owning thread has not published it.
        c.jobs.cancel()
        c.poll()
        self.assertFalse(c.busy)
        self.assertIsNone(c.state.result)
        self.assertFalse(self.audio.playing)
        self.assertEqual(c.state.project['notes'], self.notes)
        c.begin_export()
        self.finish()
        self.assertFalse(self.audio.playing)
        self.assertFalse(c.state.export_dirty)

    def test_stale_revision_result_is_not_installed(self):
        c = self.controller
        c.listen()
        # Simulate document replacement at the state boundary before a late completion.
        replacement = make_project([], '新工程')
        c.state.set_project(replacement)
        self.finish()
        self.assertEqual(c.state.project, replacement)
        self.assertIsNone(c.state.result)
        self.assertFalse(self.audio.playing)

    def test_remote_background_failure_is_reported_without_desktop_error_request(self):
        events = []
        self.controller.subscribe(lambda event, value: events.append((event, value)))
        self.assertTrue(self.remote.handle({'action': 'play'})['ok'])
        with patch('harmonica_studio.service.render_wav', side_effect=OSError('disk')), self.assertLogs(level='ERROR'):
            self.finish()
        errors = [value for event, value in events if event == 'error']
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0][1])
        self.assertFalse(self.remote.snapshot()['busy'])
        self.assertTrue(self.remote.snapshot()['can_play'])
        self.assertFalse(self.audio.playing)

    def test_preferences_change_export_validity_only_for_playback_changes(self):
        c = self.controller
        c.begin_export()
        self.finish()
        folder, revision = c.state.result[0], c.state.revision
        c.update_preferences(replace(c.preferences, theme='forest'))
        self.assertEqual(c.state.revision, revision)
        self.assertEqual(c.state.result[0], folder)
        self.assertFalse(c.state.export_dirty)
        c.update_preferences(replace(c.preferences, skip_long_rests=not c.preferences.skip_long_rests))
        self.assertGreater(c.state.revision, revision)
        self.assertTrue(c.state.export_dirty)
        self.assertTrue(c.state.project_dirty)
        self.assertEqual(c.state.project['notes'], self.notes)

    def test_remote_cache_follows_document_revision_and_not_playback_ticks(self):
        c = self.controller
        initial = self.remote.score_snapshot()
        self.assertIs(self.remote.score_snapshot(), initial)
        edited = deepcopy(self.notes)
        edited[0]['pitch'] += 1
        c.replace_notes(edited)
        changed = self.remote.score_snapshot()
        self.assertNotEqual(changed['id'], initial['id'])
        c.listen()
        self.finish()
        exported = self.remote.score_snapshot()
        self.audio.advance(.05)
        c.update_playback()
        self.assertIs(self.remote.score_snapshot(), exported)

    def test_close_with_running_worker_ignores_late_completion(self):
        c = self.controller
        c.listen()
        future, function, args, kwargs = self.executor.pending.pop()
        self.assertTrue(future.set_running_or_notify_cancel())
        events = []
        c.subscribe(lambda event, value: events.append(event))
        c.close()
        events.clear()
        future.set_result(('unused', {}))
        c.poll()
        self.assertEqual(events, [])
        self.assertTrue(c.state.closed)
        self.assertFalse(self.audio.playing)
        self.assertFalse(self.remote.handle({'action': 'play'})['ok'])
        self.assertEqual(load_project(c.home / 'autosave.hstudio')['notes'], self.notes)

    def test_failed_shutdown_save_keeps_application_usable(self):
        c = self.controller
        with patch('harmonica_studio.controller.save_project', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                c.close()
        self.assertFalse(c.state.closed)
        self.assertFalse(c.jobs.closed)
        c.listen()
        self.finish()
        self.assertTrue(self.audio.playing)

    def test_remote_selection_pipeline_is_independent_of_desktop(self):
        c = self.controller
        music = self.root / 'music'
        music.mkdir()
        write_midi(self.notes, music / 'short.mid')
        c.update_preferences(replace(c.preferences, library_folder=str(music)))
        c.refresh_library()
        song = self.remote.snapshot()['library'][0]['id']
        self.assertTrue(self.remote.handle(dict(action='select', song_id=song, autoplay=True))['ok'])
        self.finish()
        self.assertTrue(c.busy)
        self.assertTrue(self.remote.handle(dict(action='stop'))['ok'])
        self.finish()
        self.assertFalse(self.audio.playing)
        self.assertFalse(c.state.export_dirty)
        self.assertEqual(len(c.state.project['notes']), len(self.notes))

    def test_arming_after_generation_waits_for_user_start_instead_of_autoplay(self):
        self.controller.game_play(arm=True)
        self.finish()
        self.assertEqual(self.player.calls[-1][0], 'arm')
        self.assertFalse(any(call[0] == 'play' for call in self.player.calls))

    def test_modal_ui_gate_rejects_remote_play_but_keeps_stop_available(self):
        remote = RemoteControl(self.controller, blocked=lambda: True)
        self.controller.listen()
        self.assertFalse(remote.handle({'action': 'play'})['ok'])
        self.assertTrue(remote.handle({'action': 'stop'})['ok'])
        self.finish()
        self.assertFalse(self.audio.playing)


if __name__ == '__main__':
    unittest.main()
