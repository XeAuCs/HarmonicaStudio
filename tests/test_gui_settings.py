from copy import deepcopy
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PySide6.QtCore import QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from harmonica_studio.gui import MainWindow
    from harmonica_studio.diagnostics import FakeAudio
    from harmonica_studio.paths import resource_root
    from harmonica_studio.preferences import Preferences,load_preferences,save_preferences
    from harmonica_studio.settings_ui import SettingsDialog
    from harmonica_studio.theme import theme_palette
    QT_AVAILABLE=True
except ImportError:
    QT_AVAILABLE=False


@unittest.skipUnless(QT_AVAILABLE,'PySide6 is unavailable')
class SettingsWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.home=self.root/'data';self.folder=self.root/'music';self.folder.mkdir()
        save_preferences(self.home/'preferences.json',Preferences(library_folder=str(self.folder)))
        self.window=MainWindow(home=self.home,audio=FakeAudio());self.errors=[]
        self.window.show_error=lambda error:self.errors.append(str(error))
        self.window.show();self.app.processEvents()

    def tearDown(self):
        self.finish();self.window.close();self.app.processEvents();self.temp.cleanup()
        self.assertEqual(self.errors,[])

    def wait_for(self,condition):
        deadline=time.monotonic()+15
        while not condition():
            if time.monotonic()>deadline:self.fail('GUI condition timed out')
            self.app.processEvents();QTest.qWait(10)

    def finish(self):
        self.wait_for(lambda:self.window.future is None)

    def add_song(self):
        song=self.folder/'测试曲.mid'
        shutil.copyfile(resource_root()/'samples/欢乐颂.mid',song)
        self.wait_for(lambda:len(self.window.library_actions)==1)
        return song

    def choose_mode(self,compact):
        def select_mode():
            dialog=self.app.activeModalWidget()
            dialog.compact.setChecked(compact);dialog.accept()
        QTimer.singleShot(20,select_mode);self.window.settings_button.click()
        self.assertEqual(self.window.compact,compact)

    def test_folder_watcher_updates_library_without_restarting(self):
        song=self.add_song()
        self.assertEqual(self.window.library_actions[0].data(),song.name)
        song.unlink();self.wait_for(lambda:not self.window.library_actions)
        self.folder.rmdir()
        self.wait_for(lambda:str(self.folder) not in self.window.library_watcher.directories())
        self.folder.mkdir();self.add_song()

    def test_compact_auto_generates_and_mode_switch_preserves_edits(self):
        self.add_song();self.window.speed.setValue(1.7);self.window.transpose.setValue(7)
        self.choose_mode(True)
        self.window.library_actions[0].trigger();self.finish()
        self.assertTrue(self.window.compact)
        self.assertTrue(self.window.project)
        self.assertEqual(self.window.project['options']['speed'],1)
        self.assertEqual(self.window.project['options']['transpose'],0)
        self.assertTrue(self.window.edit_toolbar.isHidden())
        self.assertFalse(self.window.tabs.isTabVisible(0))
        self.choose_mode(False)
        notes=deepcopy(self.window.project['notes']);notes[0]['pitch']+=1
        self.window.notes_changed(notes)
        self.choose_mode(True);self.choose_mode(False)
        self.assertEqual(self.window.project['notes'],notes)
        self.assertTrue(self.window.export_dirty)
        self.assertEqual(self.window.speed.value(),1.7)
        self.assertEqual(self.window.transpose.value(),7)

    def test_theme_preview_cancel_and_save_persist(self):
        observed=[]
        def cancel_preview():
            dialog=self.app.activeModalWidget()
            observed.append(isinstance(dialog,SettingsDialog))
            dialog.theme.setCurrentIndex(dialog.theme.findData('blue'))
            observed.append(self.window.roll._theme['accent'].name()==theme_palette('blue')['accent'].lower())
            dialog.reject()
        QTimer.singleShot(20,cancel_preview);self.window.open_settings()
        self.assertEqual(observed,[True,True])
        self.assertEqual(self.window.roll._theme['accent'].name(),theme_palette('paper')['accent'].lower())
        def save_settings():
            dialog=self.app.activeModalWidget()
            dialog.theme.setCurrentIndex(dialog.theme.findData('plum'));dialog.compact.setChecked(True);dialog.accept()
        QTimer.singleShot(20,save_settings);self.window.open_settings()
        stored=load_preferences(self.home/'preferences.json')
        self.assertEqual((stored.theme,stored.compact),('plum',True))
        self.window.close();self.app.processEvents()
        self.window=MainWindow(home=self.home,audio=FakeAudio());self.window.show_error=lambda error:self.errors.append(str(error))
        self.assertTrue(self.window.compact)
        self.assertEqual(self.window.roll._theme['accent'].name(),theme_palette('plum')['accent'].lower())

    def test_visual_playhead_advances_between_audio_polls_and_freezes_on_pause(self):
        self.window.load_example();self.finish();self.window.convert_button.click();self.finish()
        self.window.timer.stop();started=time.perf_counter();self.window.listen_button.click()
        QTest.qWait(70)
        self.assertGreater(self.window.playback_progress.value(),20)
        self.assertLess(abs(self.window.playback_progress.value()/1000-(time.perf_counter()-started)),.1)
        self.window.audio.advance(.07);self.window.pause_button.click()
        paused=self.window.playback_progress.value();QTest.qWait(70)
        self.assertEqual(self.window.playback_progress.value(),paused)
        self.assertFalse(self.window.animation_timer.isActive())

    def test_roll_scrolls_evenly_through_note_release_in_both_modes(self):
        from harmonica_studio.project import make_project
        from harmonica_studio.service import export_project
        from harmonica_studio.transport import PlaybackClock
        notes=[dict(start=i,end=i+1,pitch=pitch,velocity=80)
               for i,pitch in enumerate((60,72,62))]
        result=export_project(make_project(notes,'连续播放'),self.home/'exports')
        self.window.show_result(*result,replace_project=True)
        self.window.timer.stop()
        for compact in (False,True):
            with self.subTest(compact=compact):
                self.window.set_compact(compact)
                now=[0.0];self.window.visual_clock=PlaybackClock(now=lambda:now[0])
                self.window.listen();self.window.animation_timer.stop()
                # Includes the 45 ms and 20 ms release gaps before both attacks.
                positions=[];offsets=[]
                for frame in range(54,132):
                    now[0]=frame/60;self.window.animate_playback()
                    positions.append(self.window.roll._position)
                    offsets.append(self.window.roll._view_offset)
                    center=(self.window.roll.LEFT+self.window.roll.viewport().width())/2
                    cursor=self.window.roll._rect(dict(start=positions[-1],end=4,pitch=60)).left()
                    self.assertAlmostEqual(cursor,center)
                for previous,current in zip(positions,positions[1:]):
                    self.assertAlmostEqual(current-previous,1/60,places=7)
                for previous,current in zip(offsets,offsets[1:]):
                    self.assertAlmostEqual(current-previous,self.window.roll._zoom/60,places=7)
                self.window.audio.position=2.2;self.window.pause_listening()
                paused=self.window.roll._view_offset;now[0]+=1;self.window.animate_playback()
                self.assertEqual(self.window.roll._view_offset,paused)
                self.window.seek_editor(1.5)
                self.assertAlmostEqual(self.window.audio.position,1.6)
                self.assertAlmostEqual(self.window.roll._position,1.5)
                self.window.stop_listening()
                self.assertEqual(self.window.project['notes'],notes)

    def test_default_library_stays_portable_after_saving_theme(self):
        dialog=SettingsDialog(Preferences(),resource_root()/'samples',parent=self.window)
        dialog.theme.setCurrentIndex(dialog.theme.findData('forest'))
        self.assertEqual(dialog.preferences().library_folder,'')
        dialog.deleteLater()

    def test_long_rests_reexport_current_score_and_restore_without_changing_notes(self):
        from harmonica_studio.project import make_project,save_project
        notes=[dict(start=0,end=5,pitch=60,velocity=80),
               dict(start=15,end=16,pitch=62,velocity=90)]
        path=self.root/'旧工程.hstudio';save_project(path,make_project(notes,'长音与空白'))
        self.window.open_project(path)
        self.assertTrue(self.window.project['options']['skip_long_rests'])
        pending_score=self.window.remote_score_snapshot()
        self.assertEqual(pending_score['duration'],self.window.logical_duration())
        self.window.begin_export();self.finish()
        short_duration=self.window.preview_duration
        expected_score=self.window.remote_score_snapshot()
        self.assertNotEqual(expected_score['id'],pending_score['id'])
        self.assertAlmostEqual(expected_score['notes'][1][0]-expected_score['notes'][0][1],.6,places=3)
        self.assertEqual(self.window.project['report']['skipped_long_rests'],1)
        self.assertIn('9.4 秒空白',self.window.summary.text())
        self.assertEqual(self.window.project['notes'],notes)
        self.window.seek_editor(3);self.window.listen()
        def turn_off():
            dialog=self.app.activeModalWidget();dialog.skip_long_rests.setChecked(False);dialog.accept()
        QTimer.singleShot(20,turn_off);self.window.open_settings()
        self.assertFalse(self.window.preferences.skip_long_rests)
        self.assertFalse(self.window.audio.playing)
        self.assertIsNone(self.window.result)
        self.assertTrue(self.window.export_dirty)
        self.assertEqual(self.window.time_anchors,[])
        self.assertEqual(self.window.project['notes'],notes)
        self.assertNotEqual(self.window.remote_score_snapshot()['id'],expected_score['id'])
        self.window.begin_export();self.finish()
        self.assertAlmostEqual(self.window.preview_duration-short_duration,9.4,places=3)
        self.assertEqual(self.window.project['report']['skipped_long_rests'],0)
        self.assertEqual(self.window.project['notes'],notes)
        self.assertFalse(load_preferences(self.home/'preferences.json').skip_long_rests)
        original_folder=self.window.result[0]
        def only_theme():
            dialog=self.app.activeModalWidget();dialog.theme.setCurrentIndex(dialog.theme.findData('blue'));dialog.accept()
        QTimer.singleShot(20,only_theme);self.window.open_settings()
        self.assertEqual(self.window.result[0],original_folder)
        self.assertFalse(self.window.export_dirty)

    def test_long_rest_setting_applies_to_both_modes_and_busy_dialog(self):
        from dataclasses import replace
        self.window.load_example();self.finish()
        for enabled in (True,False):
            self.window.preferences=replace(self.window.preferences,skip_long_rests=enabled)
            for compact in (False,True):
                self.window.compact=compact
                self.assertEqual(self.window.selected_options().skip_long_rests,enabled)
        dialog=SettingsDialog(self.window.preferences,self.folder,busy=True,parent=self.window)
        self.assertFalse(dialog.skip_long_rests.isEnabled())
        dialog.deleteLater()

    def test_cancelled_long_rest_setting_keeps_export(self):
        self.window.load_example();self.finish();self.window.begin_convert();self.finish()
        before=self.window.result
        def cancel():
            dialog=self.app.activeModalWidget();dialog.skip_long_rests.setChecked(False);dialog.reject()
        QTimer.singleShot(20,cancel);self.window.open_settings()
        self.assertIs(self.window.result,before)
        self.assertTrue(self.window.preferences.skip_long_rests)

    def test_cancelled_mode_setting_leaves_current_view_unchanged(self):
        def cancel_mode():
            dialog=self.app.activeModalWidget();dialog.compact.setChecked(True);dialog.reject()
        QTimer.singleShot(20,cancel_mode);self.window.settings_button.click()
        self.assertFalse(self.window.compact)
        self.assertFalse(load_preferences(self.home/'preferences.json').compact)
        self.assertTrue(self.window.tabs.isTabVisible(0))

    def test_compact_can_retry_a_cancelled_first_conversion(self):
        from concurrent.futures import Future
        self.window.load_example();self.finish()
        # A completed cancellation models the worker's real result at the UI boundary.
        self.window.compact=True;cancelled=Future();cancelled.set_exception(InterruptedError('转换已取消。'))
        self.window.future=cancelled;self.window.job_kind='convert';self.window.poll()
        self.assertTrue(self.window.listen_button.isEnabled())
        self.assertEqual(self.window.listen_button.text(),'重新生成')
        self.window.listen_button.click();self.finish()
        self.assertTrue(self.window.project)
