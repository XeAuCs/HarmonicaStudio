"""Behavior contracts for moving desktop/remote workflows into a controller.

Only the fixture and action helpers should need adapting during that move.
Workers finish explicitly; conversion and file persistence remain real.
"""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    import PySide6
except ModuleNotFoundError:
    QT_AVAILABLE = False
else:
    from PySide6.QtWidgets import QApplication
    from harmonica_studio.gui import MainWindow
    QT_AVAILABLE = True

from workflow_fakes import ControlledExecutor, SilentScriptPlayer
from harmonica_studio.controller import AppController
from harmonica_studio.diagnostics import FakeAudio
from harmonica_studio.midi import write_midi
from harmonica_studio.preferences import Preferences, save_preferences
from harmonica_studio.project import load_project, make_project, save_project






@unittest.skipUnless(QT_AVAILABLE, 'Workflow regressions require PySide6-Essentials')
class WorkflowRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='harmonica-workflow-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / 'data'
        self.music = self.root / 'music'
        self.music.mkdir()
        save_preferences(self.home / 'preferences.json', Preferences(library_folder=str(self.music)))
        self.executor = ControlledExecutor()
        self.audio = FakeAudio()
        self.player = SilentScriptPlayer()
        self.warnings = []
        warning = patch('harmonica_studio.gui.QMessageBox.warning',
                        side_effect=lambda parent, title, message: self.warnings.append(message))
        warning.start()
        self.addCleanup(warning.stop)
        self.window = MainWindow(controller=AppController(self.home, audio=self.audio,
                                                         player=self.player, executor=self.executor))
        self.addCleanup(self.close_window)
        self.window.show()
        self.app.processEvents()
        self.window.timer.stop()
        self.window.library_timer.stop()
        self.notes = [dict(pitch=60, start=0, end=.1, velocity=80),
                      dict(pitch=62, start=.2, end=.3, velocity=90)]
        self.source = self.root / 'original.hstudio'
        save_project(self.source, make_project(self.notes, '回归曲谱'))
        self.window.open_project(self.source)
        self.expected_warning_count = 0

    def tearDown(self):
        self.assertEqual(len(self.warnings), self.expected_warning_count,
                         'Unexpected user-facing errors: ' + repr(self.warnings))

    def close_window(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def finish_next(self):
        self.assertTrue(self.executor.pending, 'Expected a submitted background job')
        self.executor.finish_next()
        self.window.poll()
        self.window.animation_timer.stop()

    def remote(self, action, **parameters):
        result = self.window.handle_remote_command(dict(action=action, **parameters))
        self.assertTrue(result['ok'], result)

    def edit_pitch(self):
        notes = deepcopy(self.window.controller.state.project['notes'])
        notes[0]['pitch'] = 64
        self.window.notes_changed(notes)
        self.window.autosave_timer.stop()
        return notes

    def assert_not_played(self):
        self.assertFalse(self.audio.playing)
        self.assertFalse(any(call[0] == 'play' for call in self.audio.calls))
        self.assertFalse(any(call[0] == 'play' for call in self.player.calls))

    def test_manual_save_does_not_make_stale_export_current(self):
        self.window.begin_export()
        self.finish_next()
        old_folder = self.window.controller.state.result[0]
        edited = self.edit_pitch()
        destination = self.root / 'saved.hstudio'
        self.window.save_to(destination)
        self.assertEqual(load_project(destination)['notes'], edited)
        self.assertFalse(self.window.controller.state.project_dirty)
        self.assertTrue(self.window.controller.state.export_dirty)
        self.assertFalse(self.window.folder_button.isEnabled())
        self.window.listen_button.click()
        self.assertFalse(self.audio.playing)
        self.finish_next()
        self.assertTrue(self.audio.playing)
        self.assertNotEqual(self.window.controller.state.result[0], old_folder)
        self.assertEqual(load_project(old_folder / '工程.hstudio')['notes'], self.notes)

    def test_export_does_not_mark_unsaved_edits_as_manually_saved(self):
        edited = self.edit_pitch()
        self.window.export_button.click()
        self.finish_next()
        self.assertTrue(self.window.controller.state.project_dirty)
        self.assertFalse(self.window.controller.state.export_dirty)
        self.assertEqual(load_project(self.source)['notes'], self.notes)
        self.assertEqual(load_project(self.home / 'autosave.hstudio')['notes'], edited)
        self.assertEqual(load_project(self.window.controller.state.result[0] / '工程.hstudio')['notes'], edited)

    def test_remote_stop_during_preview_generation_cancels_autoplay(self):
        self.remote('play')
        self.assertTrue(self.window.remote_snapshot()['busy'])
        self.remote('stop')
        self.finish_next()
        self.assert_not_played()
        self.assertFalse(self.window.remote_snapshot()['busy'])
        self.window.listen_button.click()
        self.assertTrue(self.audio.playing)
        self.assertFalse(self.executor.pending)

    def test_desktop_stop_during_preview_generation_cancels_autoplay(self):
        self.window.listen_button.click()
        self.assertTrue(self.window.quiet_button.isEnabled())
        self.window.quiet_button.click()
        self.finish_next()
        self.assert_not_played()
        self.window.listen_button.click()
        self.assertTrue(self.audio.playing)

    def test_remote_game_stop_during_generation_cancels_pending_performance(self):
        self.remote('game_play')
        self.remote('game_stop')
        self.finish_next()
        self.assert_not_played()
        self.remote('game_play')
        self.assertEqual(self.player.calls[-1][0], 'play')
        self.assertFalse(self.executor.pending)

    def check_stop_during_selection(self, finish_load_first):
        write_midi(self.notes, self.music / '短曲.mid')
        self.window.refresh_library()
        song = self.window.remote_snapshot()['library'][0]['id']
        self.remote('select', song_id=song, autoplay=True)
        if finish_load_first:
            self.finish_next()
        self.remote('stop')
        if not finish_load_first:
            self.finish_next()
        self.finish_next()
        self.assert_not_played()
        self.assertFalse(self.window.remote_snapshot()['busy'])
        self.assertTrue(self.window.remote_snapshot()['can_play'])

    def test_stop_while_loading_song_prevents_later_autoplay(self):
        self.check_stop_during_selection(False)

    def test_stop_while_converting_selected_song_prevents_later_autoplay(self):
        self.check_stop_during_selection(True)

    def test_cancelled_export_preserves_edits_cleans_partial_files_and_can_retry(self):
        edited = self.edit_pitch()
        self.window.listen_button.click()
        self.window.cancel_button.click()
        self.finish_next()
        self.assert_not_played()
        self.assertEqual(self.window.controller.state.project['notes'], edited)
        self.assertTrue(self.window.controller.state.project_dirty)
        self.assertTrue(self.window.controller.state.export_dirty)
        self.assertEqual(list((self.home / 'exports').iterdir()), [])
        self.assertEqual(self.warnings, [])
        self.window.listen_button.click()
        self.finish_next()
        self.assertTrue(self.audio.playing)

    def test_failed_export_preserves_edits_and_retry_uses_fresh_request(self):
        edited = self.edit_pitch()
        self.expected_warning_count = 1
        with patch('harmonica_studio.service.render_wav', side_effect=OSError('injected disk failure')):
            self.window.listen_button.click()
            with self.assertLogs(level='ERROR') as captured:
                self.finish_next()
        self.assertIn('injected disk failure', '\n'.join(captured.output))
        self.assert_not_played()
        self.assertEqual(self.window.controller.state.project['notes'], edited)
        self.assertTrue(self.window.controller.state.project_dirty)
        self.assertTrue(self.window.controller.state.export_dirty)
        self.assertEqual(list((self.home / 'exports').iterdir()), [])
        self.assertEqual(len(self.warnings), 1)
        self.assertIn('injected disk failure', self.warnings[0])
        self.window.export_button.click()
        self.finish_next()
        self.assert_not_played()
        self.assertFalse(self.window.controller.state.export_dirty)

    def test_failed_manual_save_preserves_previous_file_and_unsaved_state(self):
        original = self.source.read_bytes()
        edited = self.edit_pitch()
        with patch('harmonica_studio.project.Path.replace', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                self.window.save_to(self.source)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.*.tmp')), [])
        self.assertEqual(self.window.controller.state.project['notes'], edited)
        self.assertTrue(self.window.controller.state.project_dirty)
        self.window.save_to(self.source)
        self.assertEqual(load_project(self.source)['notes'], edited)

    def test_switching_documents_preserves_unsaved_work_in_recovery(self):
        edited = self.edit_pitch()
        other = self.root / 'other.hstudio'
        save_project(other, make_project([], '空工程'))
        self.window.open_project(other)
        recovery = list((self.home / 'recovery').glob('*.hstudio'))
        self.assertEqual(len(recovery), 1)
        self.assertEqual(load_project(recovery[0])['notes'], edited)
        self.assertEqual(load_project(self.source)['notes'], self.notes)
        self.assertEqual(self.window.controller.state.project['notes'], [])
        self.assertTrue(self.window.save_button.isEnabled())
        self.assertFalse(self.window.listen_button.isEnabled())

    def test_corrupt_project_cannot_replace_current_unsaved_work(self):
        edited = self.edit_pitch()
        bad = self.root / 'broken.hstudio'
        bad.write_text('{bad', encoding='utf-8')
        self.expected_warning_count = 1
        with self.assertLogs(level='ERROR'):
            self.window.open_project(bad)
        self.assertEqual(self.window.controller.state.project['notes'], edited)
        self.assertEqual(self.window.controller.state.project_path, self.source)
        self.assertTrue(self.window.controller.state.project_dirty)
        self.assertEqual(len(self.warnings), 1)

    def test_close_during_generation_saves_edits_and_cancels_queued_work(self):
        edited = self.edit_pitch()
        self.window.listen_button.click()
        self.assertTrue(self.executor.pending)
        self.assertTrue(self.window.close())
        self.assertEqual(load_project(self.home / 'autosave.hstudio')['notes'], edited)
        self.assertFalse(self.executor.pending)
        self.assert_not_played()
        self.assertFalse((self.home / 'exports').exists())


if __name__ == '__main__':
    unittest.main()
