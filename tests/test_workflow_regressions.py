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

from workflow_fakes import ControlledExecutor, SilentScriptPlayer, finish_saves
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
        self.library_executor = ControlledExecutor()
        self.save_executor = ControlledExecutor()
        self.audio = FakeAudio()
        self.player = SilentScriptPlayer()
        self.warnings = []
        warning = patch('harmonica_studio.gui.QMessageBox.warning',
                        side_effect=lambda parent, title, message: self.warnings.append(message))
        warning.start()
        self.addCleanup(warning.stop)
        self.window = MainWindow(controller=AppController(self.home, audio=self.audio,
                                                         player=self.player, executor=self.executor,
                                                         library_executor=self.library_executor,
                                                         save_executor=self.save_executor))
        self.addCleanup(self.close_window)
        self.window.show()
        self.app.processEvents()
        self.window.timer.stop()
        self.window.library_timer.stop()
        self.library_executor.finish_next()
        self.window.poll()
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
        self.window.controller.close(wait=False)
        finish_saves(self.window.controller, self.save_executor)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def finish_next(self):
        finish_saves(self.window.controller, self.save_executor)
        self.assertTrue(self.executor.pending, 'Expected a submitted background job')
        self.executor.finish_next()
        self.window.poll()
        finish_saves(self.window.controller, self.save_executor)
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

    def test_drag_roll_pauses_preview_seeks_once_and_resumes_from_release(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        window, c = self.window, self.window.controller
        notes = [dict(pitch=60 + i % 12, start=i * .5, end=i * .5 + .4, velocity=80)
                 for i in range(24)]
        save_project(self.source, make_project(notes, '拖动测试'))
        window.open_project(self.source)
        window.listen()
        self.finish_next()
        for compact in (True, False):
            with self.subTest(compact=compact):
                window.set_compact(compact)
                window.seek_editor(5)
                window.listen()
                window.animation_timer.stop()
                self.audio.calls.clear()
                roll = window.roll
                start = QPoint(round(roll.LEFT + roll._timeline_width() / 2), 12)
                QTest.mousePress(roll.viewport(), Qt.LeftButton, Qt.NoModifier, start)
                self.assertTrue(self.audio.playing)  # A press alone is not a drag.
                for dx in (20, 40, 70):
                    QTest.mouseMove(roll.viewport(), start - QPoint(dx, 0))
                self.assertFalse(self.audio.playing)
                self.assertEqual(c.state.transport, 'paused')
                self.assertFalse(window.animation_timer.isActive())
                self.assertEqual([call[0] for call in self.audio.calls], ['pause'])
                self.assertAlmostEqual(roll._position, 6)
                QTest.mouseRelease(roll.viewport(), Qt.LeftButton, Qt.NoModifier, start - QPoint(70, 0))
                self.assertEqual([call[0] for call in self.audio.calls], ['pause', 'seek'])
                self.assertFalse(self.audio.playing)
                self.assertEqual(c.state.transport, 'paused')
                self.assertAlmostEqual(c.state.logical_seek, 6)
                self.assertAlmostEqual(self.audio.position, c.to_audio(6))
                self.assertEqual(c.state.project['notes'], notes)
                window.listen()
                window.animation_timer.stop()
                self.assertTrue(self.audio.playing)
                self.assertAlmostEqual(self.audio.calls[-1][1], c.to_audio(6))

    def test_drag_before_preview_generation_keeps_seek_for_first_listen(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        window, c = self.window, self.window.controller
        window.set_compact(True)
        roll = window.roll
        window.seek_editor(.1)
        start = QPoint(400, 90)
        QTest.mousePress(roll.viewport(), Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(roll.viewport(), start - QPoint(10, 0))
        QTest.mouseRelease(roll.viewport(), Qt.LeftButton, Qt.NoModifier, start - QPoint(10, 0))
        expected = .1 + 10 / roll._zoom
        self.assertAlmostEqual(c.state.logical_seek, expected)
        self.assertFalse(self.audio.playing)
        window.listen()
        self.assertFalse(roll._navigation_enabled)
        self.finish_next()
        self.assertTrue(self.audio.playing)
        self.assertAlmostEqual(self.audio.position, c.to_audio(expected))

    def test_manual_save_does_not_make_stale_export_current(self):
        self.window.begin_export()
        self.finish_next()
        old_folder = self.window.controller.state.result[0]
        edited = self.edit_pitch()
        destination = self.root / 'saved.hstudio'
        self.window.save_to(destination)
        finish_saves(self.window.controller, self.save_executor)
        self.assertEqual(load_project(destination)['notes'], edited)
        self.assertFalse(self.window.controller.state.project_dirty)
        self.assertTrue(self.window.controller.state.export_dirty)
        self.assertFalse(self.window.controller.capabilities()['current_export'])
        self.window.listen_button.click()
        self.assertFalse(self.audio.playing)
        self.finish_next()
        self.assertTrue(self.audio.playing)
        self.assertNotEqual(self.window.controller.state.result[0], old_folder)
        self.assertEqual(load_project(old_folder / '工程.hstudio')['notes'], self.notes)

    def test_background_refresh_preserves_actions_until_open_menu_closes(self):
        window = self.window
        write_midi(self.notes, self.music / 'first.mid')
        window.refresh_library()
        self.library_executor.finish_next()
        window.poll()
        action = window.library_actions[0]
        window.sample_menu.popup(window.example_button.mapToGlobal(window.example_button.rect().bottomLeft()))
        try:
            self.assertTrue(window.sample_menu.isVisible())
            write_midi(self.notes, self.music / 'second.mid')
            self.library_executor.finish_next()
            window.poll()
            self.assertEqual(len(window.controller.library), 2)
            self.assertEqual(window.library_actions, [action])
            self.assertTrue(window._library_render_pending)
            self.assertTrue(window.save_shortcut.isEnabled())
            self.assertTrue(window.listen_button.isEnabled())
        finally:
            window.sample_menu.hide()
            self.app.processEvents()
            window.library_timer.stop()
        self.assertEqual(len(window.library_actions), 2)
        self.assertFalse(window._library_render_pending)

    def test_preview_generation_autosaves_edits_to_open_project(self):
        edited = self.edit_pitch()
        self.window.listen_button.click()
        self.finish_next()
        self.assertTrue(self.audio.playing)
        self.assertFalse(self.window.controller.state.project_dirty)
        self.assertFalse(self.window.controller.state.export_dirty)
        self.assertEqual(load_project(self.source)['notes'], edited)
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

    def test_desktop_cancel_during_preview_generation_prevents_autoplay(self):
        self.window.listen_button.click()
        self.assertTrue(self.window.cancel_button.isEnabled())
        self.assertTrue(self.window.cancel_button.isVisible())
        self.window.cancel_button.click()
        self.finish_next()
        self.assert_not_played()
        self.window.listen_button.click()
        self.finish_next()
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
        self.library_executor.finish_next()
        self.window.poll()
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
        self.window.listen_button.click()
        self.finish_next()
        self.assertTrue(self.audio.playing)
        self.assertFalse(self.window.controller.state.export_dirty)

    def test_failed_manual_save_preserves_previous_file_and_unsaved_state(self):
        original = self.source.read_bytes()
        edited = self.edit_pitch()
        self.expected_warning_count = 1
        with patch('harmonica_studio.project.Path.replace', side_effect=PermissionError('locked')):
            with self.assertLogs(level='ERROR'):
                self.window.save_to(self.source)
                finish_saves(self.window.controller, self.save_executor)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.*.tmp')), [])
        self.assertEqual(self.window.controller.state.project['notes'], edited)
        self.assertTrue(self.window.controller.state.project_dirty)
        self.window.save_to(self.source)
        finish_saves(self.window.controller, self.save_executor)
        self.assertEqual(load_project(self.source)['notes'], edited)

    def test_switching_documents_preserves_unsaved_work_in_recovery(self):
        edited = self.edit_pitch()
        other = self.root / 'other.hstudio'
        save_project(other, make_project([], '空工程'))
        self.window.open_project(other)
        finish_saves(self.window.controller, self.save_executor)
        recovery = list((self.home / 'recovery').glob('*.hstudio'))
        self.assertEqual(len(recovery), 1)
        self.assertEqual(load_project(recovery[0])['notes'], edited)
        self.assertEqual(load_project(self.source)['notes'], edited)
        self.assertEqual(self.window.controller.state.project['notes'], [])
        self.assertTrue(self.window.save_shortcut.isEnabled())
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
        self.assertFalse(self.window.close())
        finish_saves(self.window.controller, self.save_executor)
        self.assertTrue(self.window.controller.state.closed)
        self.assertEqual(load_project(self.home / 'autosave.hstudio')['notes'], edited)
        self.assertFalse(self.executor.pending)
        self.assert_not_played()
        self.assertFalse((self.home / 'exports').exists())

    def test_async_close_failure_keeps_window_and_restores_editing(self):
        self.edit_pitch()
        self.expected_warning_count = 1
        self.assertFalse(self.window.close())
        self.assertFalse(self.window.save_shortcut.isEnabled())
        self.assertFalse(self.window.open_button.isEnabled())
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        with patch('harmonica_studio.save_coordinator.save_project', side_effect=OSError('disk')), \
                self.assertLogs(level='ERROR'):
            finish_saves(self.window.controller, self.save_executor)
        self.assertFalse(self.window.controller.state.closed)
        self.assertTrue(self.window.controller.state.project_dirty)
        self.assertTrue(self.window.save_shortcut.isEnabled())
        self.assertTrue(self.window.open_button.isEnabled())
        self.assertTrue(self.window.isVisible())

    def test_project_shortcut_and_file_drop_work_without_project_buttons(self):
        from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
        from PySide6.QtGui import QDragEnterEvent, QDropEvent, QKeySequence
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton
        window = self.window
        for compact in (False, True):
            window.set_compact(compact)
            window.activateWindow()
            window.setFocus()
            self.app.processEvents()
            self.assertFalse(any(b.text() in ('打开工程', '保存工程') for b in window.findChildren(QPushButton)))
            destination = self.root / f'backup-{compact}.hstudio'
            with patch('harmonica_studio.gui.QFileDialog.getSaveFileName', return_value=(str(destination), '')) as dialog:
                QTest.keySequence(window, QKeySequence.Save)
                self.app.processEvents()
                dialog.assert_called_once()
            finish_saves(window.controller, self.save_executor)
            self.assertEqual(load_project(destination)['notes'], self.notes)
            imported = self.root / f'imported-{compact}.hstudio'
            save_project(imported, make_project(self.notes, '拖入的工程'))
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(imported))])
            enter = QDragEnterEvent(QPoint(50, 50), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            self.app.sendEvent(window, enter)
            self.assertTrue(enter.isAccepted())
            drop = QDropEvent(QPointF(50, 50), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
            self.app.sendEvent(window, drop)
            self.app.processEvents()
            self.assertEqual(window.controller.state.project_path, imported)
            self.assertEqual(window.controller.state.project['title'], '拖入的工程')

    def test_highlight_controls_save_restore_and_clear_in_both_layouts(self):
        window=self.window
        window.seek_editor(.2)
        window.highlight_button.click()
        self.assertEqual(window.controller.state.project['highlight'],.2)
        self.assertEqual(window.roll._highlight,.2)
        self.assertEqual(window.playback_progress.marker_seconds,.2)
        self.assertIn('00:00.200',window.playback_progress.toolTip())
        for compact in (True,False):
            window.set_compact(compact)
            self.app.processEvents()
            self.assertTrue(window.highlight_button.isVisible())
            self.assertTrue(window.highlight_seek_button.isEnabled())
            roll_bottom=window.roll.mapTo(window,window.roll.rect().bottomLeft()).y()
            marker_top=window.highlight_button.mapTo(window,window.highlight_button.rect().topLeft()).y()
            self.assertGreater(marker_top,roll_bottom)
            window.seek_editor(0)
            window.highlight_seek_button.click()
            self.assertAlmostEqual(window.controller.state.logical_seek,.2)
        destination=self.root/'marked.hstudio'
        window.save_to(destination)
        finish_saves(window.controller,self.save_executor)
        window.open_project(destination)
        self.assertEqual(window.roll._highlight,.2)
        window.highlight_clear_button.click()
        self.assertIsNone(window.roll._highlight)
        self.assertIsNone(window.playback_progress.marker_seconds)
        self.assertFalse(window.highlight_seek_button.isEnabled())

    def test_progress_marker_follows_audio_time_after_export_and_score_time_after_edit(self):
        window=self.window
        c=window.controller
        c.set_highlight(.2)
        self.assertEqual(window.playback_progress.marker_seconds,.2)
        window.begin_export()
        self.finish_next()
        self.assertAlmostEqual(window.playback_progress.marker_seconds,c.to_audio(.2))
        self.assertNotAlmostEqual(window.playback_progress.marker_seconds,.2)
        self.assertEqual(window.roll._highlight,.2)
        self.edit_pitch()
        self.assertEqual(window.playback_progress.marker_seconds,.2)
        c.set_highlight(None)
        self.assertIsNone(window.playback_progress.marker_seconds)

    def test_compact_game_button_prepares_changed_highlight_before_arming(self):
        from dataclasses import replace
        c=self.window.controller
        c.set_highlight(.2)
        c.update_preferences(replace(c.preferences,start_from_highlight=True))
        self.window.set_compact(True)
        self.assertTrue(self.window.arm_button.isEnabled())
        self.window.arm_button.click()
        self.finish_next()
        self.assertEqual(self.player.calls[-1][0],'arm')
        self.assertAlmostEqual(self.player.calls[-1][2],c.to_audio(.2))
        self.assert_not_played()


if __name__ == '__main__':
    unittest.main()
