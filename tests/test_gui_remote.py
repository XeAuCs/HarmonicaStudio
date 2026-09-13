from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from harmonica_studio.diagnostics import FakeAudio
from harmonica_studio.gui import MainWindow
from harmonica_studio.paths import resource_root
from harmonica_studio.preferences import Preferences, save_preferences
from harmonica_studio.remote_ui import RemoteDialog, qr_pixmap
from harmonica_studio.theme import theme_palette


class FakeScriptPlayer:
    def __init__(self):self.alive=False;self.calls=[];self.status={'state':'idle','position':0,'duration':0,'message':''}
    def play(self, script):self.calls.append(('play', Path(script)));self.alive=True;self.status['state']='countdown'
    def start(self, script):self.alive=True
    def stop_playback(self):self.calls.append(('stop_playback',));self.status['state']='ready'
    def stop(self):self.alive=False;self.status['state']='idle'
    def reap(self):pass


class RemoteGuiWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        library=self.root/'music';library.mkdir()
        shutil.copyfile(resource_root()/'samples/欢乐颂.mid', library/'手机试播.mid')
        save_preferences(self.root/'data/preferences.json', Preferences(library_folder=str(library)))
        self.window=MainWindow(home=self.root/'data', audio=FakeAudio())
        self.window.controller.player=FakeScriptPlayer();self.window.show()
        self.pool=ThreadPoolExecutor(max_workers=2)
        self.wait_for(lambda:not self.window.controller.library_refreshing)
        self.server=self.window.start_remote(host='127.0.0.1',port=0)
    def tearDown(self):
        self.wait_for(lambda:self.window.controller.jobs.current is None and not self.window.controller.library_refreshing)
        self.window.controller.close();self.window.close();self.app.processEvents();self.pool.shutdown();self.temp.cleanup()
    def wait_for(self, condition):
        deadline=time.monotonic()+20
        while not condition():
            if time.monotonic()>deadline:self.fail('Remote GUI workflow timed out')
            self.app.processEvents();QTest.qWait(10)
    def request(self, command=None, path=None):
        def fetch():
            route=path or ('/api/state' if command is None else '/api/command')
            request=Request(f'http://127.0.0.1:{self.server.port}'+route,
                data=None if command is None else json.dumps(command).encode(),
                headers={'Authorization':'Bearer '+self.server.token, 'Content-Type':'application/json'})
            try:
                with urlopen(request, timeout=5) as response:return json.load(response)
            except HTTPError as response:return json.load(response)
        result=self.pool.submit(fetch);self.wait_for(result.done);return result.result()
    def select(self, autoplay=False):
        state=self.request();song=state['library'][0]
        self.assertTrue(self.request({'action':'select','song_id':song['id'],'autoplay':autoplay})['ok'])
        self.wait_for(lambda:self.window.controller.jobs.current is None and self.window.controller.state.transition is None)
        self.assertIsNotNone(self.window.controller.state.project)
        self.window.poll_remote()
    def test_phone_select_prepares_full_mode_and_transport_controls_desktop(self):
        self.assertFalse(self.window.compact)
        self.select();self.assertEqual(self.window.controller.state.transport,'ready')
        self.assertTrue(self.request({'action':'play'})['ok']);self.assertTrue(self.window.controller.audio.playing)
        self.request({'action':'seek','position':7.5});self.assertAlmostEqual(self.window.controller.audio.position,7.5)
        self.request({'action':'pause'});self.assertEqual(self.window.controller.state.transport,'paused')
        self.request({'action':'play'});self.assertEqual(self.window.controller.state.transport,'playing')
        self.request({'action':'stop'});self.assertEqual(self.window.controller.audio.position,0)
        self.assertFalse(self.window.controller.audio.playing)
    def test_library_and_selection_do_not_expose_filesystem_paths(self):
        state=self.request()
        self.assertNotIn(str(self.root),json.dumps(state,ensure_ascii=False))
        self.assertEqual(state['library'][0]['title'],'手机试播')
        self.select();self.assertEqual(self.request()['song_id'],state['library'][0]['id'])
        self.assertFalse(self.request({'action':'select','song_id':'../outside.mid'})['ok'])
    def test_autoplay_and_live_desktop_progress(self):
        self.select(autoplay=True);self.assertTrue(self.window.controller.audio.playing)
        self.window.controller.audio.advance(3);self.window.update_playback();self.window.poll_remote()
        self.assertAlmostEqual(self.request()['position'],3,delta=.3)
        self.window.pause_listening();self.window.poll_remote()
        self.assertEqual(self.request()['transport'],'paused')
    def test_remote_game_routes_to_player_without_synthetic_test_input(self):
        self.select();self.request({'action':'game_play'})
        self.assertEqual(self.window.controller.player.calls[-1][0],'play')
        self.assertFalse(self.window.controller.audio.playing)
        self.request({'action':'game_stop'})
        self.assertEqual(self.window.controller.player.calls[-1],('stop_playback',))
    def test_edited_score_is_rendered_before_phone_game_start(self):
        self.select();notes=[dict(n) for n in self.window.controller.state.project['notes']];notes[0]['pitch']+=1
        self.window.notes_changed(notes);self.assertTrue(self.window.controller.state.export_dirty)
        self.request({'action':'game_play'});self.wait_for(lambda:self.window.controller.jobs.current is None)
        self.assertFalse(self.window.controller.state.export_dirty);self.assertEqual(self.window.controller.player.calls[-1][0],'play')
    def test_qr_and_shutdown_keep_service_opt_in(self):
        pixmap=qr_pixmap(self.server.url('127.0.0.1'))
        self.assertGreater(pixmap.width(),200)
        self.assertEqual(pixmap.toImage().pixelColor(0,0).name(),'#ffffff')
        self.window.stop_remote();self.assertFalse(self.server.active)
        self.assertFalse(self.window.remote_timer.isActive())

    def test_pairing_styles_and_live_theme_keep_connection_and_network_selection(self):
        dialog=RemoteDialog(self.server,[('本机地址','127.0.0.1'),('本机名称','localhost')],self.window)
        self.window.remote_dialog=dialog;dialog.show();self.app.processEvents()
        original=dialog.url.text()
        dialog.standard.click();self.assertFalse(dialog.artistic.isChecked())
        self.assertEqual(dialog.qr.pixmap().toImage().pixelColor(0,0).name(),'#ffffff')
        self.window.apply_theme('plum')
        self.assertEqual(dialog.qr.pixmap().toImage().pixelColor(0,0).name(),'#ffffff')
        dialog.artistic.click()
        self.assertEqual(dialog.qr.pixmap().toImage().pixelColor(0,0).name(),theme_palette('plum')['surface'].lower())
        self.assertEqual(dialog.url.text(),original)
        dialog.address.setCurrentIndex(1)
        self.assertEqual(dialog.url.text(),self.server.url('localhost'))
        self.assertTrue(self.server.active)
        dialog.hide();self.assertTrue(self.server.active)

    def test_color_rounding_preserves_every_module_center_and_quiet_border(self):
        import segno
        for theme in ('paper','forest','blue','plum'):
            for length in (43,300):
                url='http://127.0.0.1:47638/#token='+'a'*length
                matrix=tuple(segno.make_qr(url,error='m').matrix)
                for ratio in (1.0,1.5,2.0):
                    with self.subTest(theme=theme,length=length,ratio=ratio):
                        pixmap=qr_pixmap(url,theme_palette(theme),device_pixel_ratio=ratio)
                        picture=pixmap.toImage()
                        scale=max(4,300//(len(matrix)+8))*ratio
                        shades=set()
                        for y,row in enumerate(matrix):
                            for x,dark in enumerate(row):
                                color=picture.pixelColor(int((x+4.5)*scale),int((y+4.5)*scale))
                                self.assertEqual(max(color.red(),color.green(),color.blue())<180,bool(dark))
                                if dark:shades.add(color.name())
                        self.assertGreater(len(shades),20)
                        paper=theme_palette(theme)['surface'].lower()
                        for edge in (int(scale),picture.width()-int(scale)-1):
                            for offset in range(picture.width()):
                                self.assertEqual(picture.pixelColor(edge,offset).name(),paper)
                                self.assertEqual(picture.pixelColor(offset,edge).name(),paper)

    def test_remote_score_matches_exported_audio_and_game_timing(self):
        self.select()
        state=self.request();score=self.request(path='/api/score')
        self.assertEqual(state['score_id'],score['id'])
        actual=json.loads((self.window.controller.state.result[0]/'音符.json').read_text(encoding='utf-8'))
        self.assertEqual(len(actual),len(score['notes']))
        for note,row in zip(actual,score['notes']):
            self.assertAlmostEqual(note['start'],row[0],places=6)
            self.assertAlmostEqual(note['end'],row[1],places=6)
            self.assertEqual(note['pitch'],row[2])
        cached=self.window.remote_score_snapshot()
        self.window.controller.audio.advance(2);self.window.poll_remote()
        self.assertIs(self.window.remote_score_snapshot(),cached)
        self.assertNotIn('notes',state)

    def test_remote_score_revision_changes_on_edit_export_and_clear(self):
        self.select();before=self.request()['score_id']
        notes=[dict(n) for n in self.window.controller.state.project['notes']];notes[0]['pitch']+=1
        self.window.notes_changed(notes);self.window.poll_remote()
        edited=self.request(path='/api/score')
        self.assertNotEqual(edited['id'],before)
        self.assertEqual(edited['notes'][0][2],notes[0]['pitch'])
        self.assertAlmostEqual(edited['notes'][0][0],notes[0]['start'],places=6)
        self.window.begin_export();self.wait_for(lambda:self.window.controller.jobs.current is None);self.window.poll_remote()
        exported=self.request(path='/api/score')
        self.assertNotEqual(exported['id'],edited['id'])
        self.window.load_file(self.root/'music/手机试播.mid')
        self.wait_for(lambda:self.window.controller.state.transition is None)
        self.window.poll_remote()
        self.assertEqual(self.request(path='/api/score')['notes'],[])
