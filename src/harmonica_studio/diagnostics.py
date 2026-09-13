"""Offscreen end-to-end verification without sound or game input."""
from copy import deepcopy
from pathlib import Path
import json, os, subprocess, sys, time, traceback, uuid, wave, shutil


class FakeAudio:
    """Controllable silent audio transport, injected only by the smoke test."""
    def __init__(self):
        self.path=None;self.duration=0.0;self.position=0.0;self.playing=False;self.calls=[]
        self.fail_next_close=False;self.fail_next_load=False
    def load(self,path):
        self.close()
        if self.fail_next_load:
            self.fail_next_load=False;raise RuntimeError('Expected diagnostic audio load failure')
        self.path=Path(path)
        with wave.open(str(self.path),'rb') as wav:
            self.duration=round(wav.getnframes()/wav.getframerate()*1000)/1000
        self.calls.append(('load',str(self.path)))
    def play(self,start_seconds=None):
        if self.path is None:raise RuntimeError('Silent test player has no audio loaded.')
        if start_seconds is not None:self.position=max(0.0,min(self.duration,start_seconds))
        self.playing=self.position<self.duration;self.calls.append(('play',self.position))
    def pause(self):self.playing=False;self.calls.append(('pause',self.position))
    def stop(self):self.playing=False;self.position=0.0;self.calls.append(('stop',))
    def seek(self,seconds,resume=False):
        if self.path is None:raise RuntimeError('Silent test player has no audio loaded.')
        self.position=max(0.0,min(self.duration,seconds));self.playing=bool(resume and self.position<self.duration)
        self.calls.append(('seek',self.position,self.playing))
    def close(self):
        if self.fail_next_close:
            self.fail_next_close=False;raise RuntimeError('Expected diagnostic audio close failure')
        self.playing=False;self.position=self.duration=0.0;self.path=None;self.calls.append(('close',))
    def advance(self,seconds):
        if self.playing:
            self.position=min(self.duration,self.position+seconds)
            if self.position==self.duration:self.playing=False


def self_test(report_path):
    report_path=Path(report_path).resolve();report_path.parent.mkdir(parents=True,exist_ok=True)
    os.environ['QT_QPA_PLATFORM']='offscreen'
    windows,errors,checks=[],[],[];app=None
    try:
        from PySide6.QtCore import QPoint,Qt
        from PySide6.QtGui import QFontDatabase
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from .gui import MainWindow,STYLE,map_time
        from .midi import read_midi
        from .paths import resource_root, default_library_root, application_root, data_root
        from .project import load_project
        app=QApplication.instance() or QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
        fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
        for name in ('msyh.ttc','msyhbd.ttc','segoeui.ttf'):
            if (fonts/name).is_file():QFontDatabase.addApplicationFont(str(fonts/name))
        home=report_path.parent/(report_path.stem+'-data-'+uuid.uuid4().hex[:6])
        # Exercise native filename handling; FakeAudio cannot expose MCI's path limit.
        from .playback import AudioPlayer
        native_folder=home/('native-long-path-'+'x'*60)
        native_folder.mkdir(parents=True)
        native_path=native_folder/'Bad Apple!!（坏家伙）-试听.wav'
        with wave.open(str(native_path),'wb') as sound:
            sound.setparams((1,2,22050,0,'NONE','not compressed'))
            sound.writeframes(b'\0\0'*22050)
        native=AudioPlayer();staged=None
        try:
            native.load(native_path)
            assert abs(native.duration-1)<.01
            native.seek(.25);assert abs(native.position-.25)<.01
            staged=Path(native._staged_audio.name) if native._staged_audio else None
        finally:native.close()
        assert staged is None or not staged.exists()
        assert native_path.is_file()
        checks.append('native Windows audio opens long Unicode paths, seeks and cleans private copies')
        from .preferences import Preferences,save_preferences,load_preferences
        from .theme import theme_palette
        from .settings_ui import SettingsDialog
        from PySide6.QtCore import QTimer
        music=home/'music';music.mkdir(parents=True)
        if getattr(sys, 'frozen', False):
            assert default_library_root() == application_root() / 'samples'
            assert data_root() == application_root() / 'data'
            assert not (resource_root() / 'samples').exists()
            checks.append('portable paths use EXE-adjacent samples and data, without an internal library')
        for file in default_library_root().iterdir():
            if file.suffix.lower() in ('.mid','.midi','.kar','.rmi') or file.name=='catalog.json':shutil.copyfile(file,music/file.name)
        save_preferences(home/'preferences.json',Preferences(library_folder=str(music)))
        def create_window():
            window=MainWindow(home=home,audio=FakeAudio());window.show_error=lambda exc:errors.append(str(exc))
            windows.append(window);window.show();app.processEvents();return window
        def finish(window,allow_errors=False):
            deadline=time.monotonic()+90
            while window.controller.jobs.current is not None:
                if time.monotonic()>deadline:raise TimeoutError('界面任务超时。')
                app.processEvents();window.poll()
                if errors and not allow_errors:raise AssertionError(errors)
                time.sleep(.01)
            app.processEvents();assert allow_errors or not errors,errors
        def choose_mode(window,compact):
            def select_mode():
                dialog=app.activeModalWidget();dialog.compact.setChecked(compact);dialog.accept()
            QTimer.singleShot(30,select_mode);window.settings_button.click()
            assert window.compact==compact
        def assert_centered(roll):
            assert roll._position is not None
            actual=roll._rect(dict(start=roll._position,end=roll._position+1,pitch=60)).left()
            assert abs(actual-(roll.LEFT+roll.viewport().width())/2)<.01,(actual,roll.viewport().width())
        def validate_ahk(folder):
            ahk=resource_root()/'third_party/AutoHotkey/AutoHotkey64.exe'
            checked=subprocess.run([str(ahk),'/ErrorStdOut',str(folder/'演奏脚本.ahk'),'--validate'],
                capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            assert checked.returncode==0,(checked.stdout,checked.stderr)
        from .library import sample_entries
        entries=sample_entries(music);assert len(entries)>=7,entries
        assert any(e['file']=='春日影.mid' for e in entries)
        window=create_window()
        sizes={size.width() for size in window.windowIcon().availableSizes()}
        assert {16,32,256}.issubset(sizes),sizes
        actions=window.library_actions
        assert [a.data() for a in actions]==[entry['file'] for entry in entries]
        for entry,action in zip(entries,actions):
            read_midi(entry['path'])
            action.trigger();finish(window)
            assert window.controller.state.source.name==entry['file'] and window.table.rowCount()>0
            if 'options' in entry:
                options=window.selected_options()
                assert (options.track,options.channel)==(entry['options']['track'],entry['options']['channel'])
                assert window.table.item(window.table.currentRow(),0).text().startswith('★ ')
        checks.extend([f'{len(entries)} MIDI examples including Haruhikage and melody presets','multi-resolution line application icon'])
        discovered=music/'自动发现测试.mid';shutil.copyfile(music/'欢乐颂.mid',discovered)
        deadline=time.monotonic()+5
        while len(window.library_actions)!=len(entries)+1 and time.monotonic()<deadline:QTest.qWait(30)
        assert any(a.data()==discovered.name for a in window.library_actions)
        discovered.unlink()
        deadline=time.monotonic()+5
        while len(window.library_actions)!=len(entries) and time.monotonic()<deadline:QTest.qWait(30)
        assert len(window.library_actions)==len(entries)
        checks.append('music folder additions and removals update without restarting')
        window.load_example();finish(window)
        assert window.table.rowCount()>=1 and window.convert_button.isEnabled()
        window.convert_button.click();finish(window)
        assert window.controller.state.result and window.listen_button.isEnabled()
        baseline_folder,baseline_report=window.controller.state.result
        assert baseline_report['melody_notes']==94 and baseline_report['dropped_out_of_range']==0
        assert (window.controller.home/'settings.json').is_file();validate_ahk(baseline_folder)
        checks.extend(['sample MIDI import and recommended melody','background conversion of 94 notes',
            'MIDI/WAV/AHK/project export and AHK validation','preferences persisted'])

        # Modify the widget using a vertical-only gesture, then exercise UI history.
        original_notes=deepcopy(window.controller.state.project['notes']);rectangle=window.roll._rect(original_notes[0])
        start=QPoint(round(rectangle.left()+rectangle.width()*.3),round(rectangle.center().y()))
        end=start+QPoint(0,-window.roll.ROW)
        assert window.roll.viewport().rect().contains(start),(start,window.roll.viewport().rect())
        window.controller.audio.fail_next_close=True
        QTest.mousePress(window.roll.viewport(),Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(window.roll.viewport(),end,10)
        QTest.mouseRelease(window.roll.viewport(),Qt.LeftButton,Qt.NoModifier,end);app.processEvents()
        edited_notes=deepcopy(window.controller.state.project['notes']);expected=deepcopy(original_notes);expected[0]['pitch']+=1
        assert edited_notes==expected,(edited_notes[0],expected[0])
        assert window.controller.state.export_dirty and window.controller.state.project_dirty
        assert not window.arm_button.isEnabled() and not window.folder_button.isEnabled()
        assert not window.controller.audio.fail_next_close and window.controller.state.preview_duration==0
        # Even a failing device close cannot lose the edit or re-enable stale exports.
        window.controller.audio.close();assert window.controller.audio.path is None
        window.undo_button.click();assert window.controller.state.project['notes']==original_notes
        window.redo_button.click();assert window.controller.state.project['notes']==edited_notes
        checks.extend(['Qt mouse gesture changes pitch while preserving exact timing',
            'edits invalidate stale audio and script actions even when audio close fails',
            'undo and redo through UI'])

        # The project must reopen and export with an unavailable source MIDI.
        missing_source=home/'original-midi-no-longer-present.mid';assert not missing_source.exists()
        window.controller.state.project['source']={'path':str(missing_source)}
        saved=home/'修订后的欢乐颂.hstudio';window.save_to(saved)
        assert load_project(saved)['notes']==edited_notes and not window.controller.state.project_dirty
        window.open_project(saved);assert not errors,errors
        assert window.controller.state.source is None and not window.controller.state.parts and window.controller.state.result is None
        assert window.controller.state.project['notes']==edited_notes and not window.arm_button.isEnabled()
        checks.append('save and reopen self-contained project with missing source MIDI')

        # Click the logical ruler before asking Listen to render current edits.
        window.roll.horizontalScrollBar().setValue(0)
        QTest.mouseClick(window.roll.viewport(),Qt.LeftButton,Qt.NoModifier,
            QPoint(round(window.roll._rect(dict(start=1.0,end=2.0,pitch=60)).left()),10))
        assert abs(window.controller.state.logical_seek-1.0)<.001,window.controller.state.logical_seek
        window.controller.audio.fail_next_load=True
        window.listen_button.click();finish(window,allow_errors=True)
        assert errors==['Expected diagnostic audio load failure'],errors
        errors.clear()
        assert window.controller.state.result and not window.controller.state.export_dirty and window.controller.state.preview_duration==0
        failed_audio_folder=window.controller.state.result[0]
        window.listen_button.click();finish(window)
        assert window.controller.state.result[0]==failed_audio_folder
        assert window.controller.state.transport=='playing' and window.controller.audio.playing
        edited_folder,edited_report=window.controller.state.result
        assert edited_folder!=baseline_folder and edited_report['edited']
        assert window.controller.state.project['notes']==edited_notes
        assert load_project(edited_folder/'工程.hstudio')['notes']==edited_notes
        actual_notes=json.loads((edited_folder/'音符.json').read_text(encoding='utf-8'))
        midi_parts,_=read_midi(edited_folder/'口琴单旋律.mid')
        assert midi_parts[(0,0)]==actual_notes and actual_notes[0]['pitch']==edited_notes[0]['pitch']
        assert abs(window.controller.audio.position-map_time(1.0,window.controller.time_anchors))<.001
        validate_ahk(edited_folder)
        checks.extend(['ruler seek before rendering','listen automatically exports current edits',
            'audio load failure retries on Listen without losing or re-exporting edits',
            'edited MIDI matches current notes and physical key timing'])

        # Key-release gaps must not freeze the centered score at note edges.
        gaps_checked=0
        for left,right in zip(actual_notes,actual_notes[1:]):
            gap=right['start']-left['end']
            if gap<=.002:continue
            offsets=[]
            for fraction in (.1,.5,.9):
                window.display_position(left['end']+gap*fraction,'试听中')
                assert_centered(window.roll);offsets.append(window.roll._view_offset)
            assert offsets[0]<offsets[1]<offsets[2],(left,right,offsets)
            gaps_checked+=1
        assert gaps_checked>0
        for logical,physical in zip(edited_notes,actual_notes):
            assert abs(window.controller.to_score(physical['start'])-logical['start'])<1e-8
        checks.append('score scrolls through every release gap while note attacks stay aligned')

        window.controller.audio.advance(2.4);window.poll()
        assert abs(window.playback_progress.value()-round(window.controller.audio.position*1000))<=5
        assert window.roll._position is not None
        window.pause_button.click();paused=window.controller.audio.position
        assert window.controller.state.transport=='paused' and not window.controller.audio.playing
        window.controller.audio.advance(3);window.poll();assert window.controller.audio.position==paused
        window.listen_button.click();assert window.controller.audio.playing and abs(window.controller.audio.position-paused)<.001
        checks.append('native-position transport UI with pause and resume (silent backend)')

        slider=window.playback_progress;point=QPoint(round(slider.width()*.2),max(1,slider.height()//2))
        destination=QPoint(round(slider.width()*.4),point.y())
        QTest.mousePress(slider,Qt.LeftButton,Qt.NoModifier,point);QTest.mouseMove(slider,destination,10)
        QTest.mouseRelease(slider,Qt.LeftButton,Qt.NoModifier,destination);window.poll()
        expected_ms=round((destination.x()-7)/(slider.width()-14)*slider.maximum())
        assert abs(window.controller.audio.position*1000-expected_ms)<=1
        assert window.controller.audio.playing and window.controller.state.transport=='playing'
        checks.append('progress slider drag seeks and continues playback')

        window.quiet_button.click()
        assert window.controller.audio.position==0 and not window.controller.audio.playing
        assert window.playback_progress.value()==0 and window.roll._position is None
        window.listen_button.click();window.controller.audio.advance(window.controller.audio.duration+1);window.poll()
        assert window.controller.state.transport=='ended' and window.playback_state.text()=='已结束'
        assert window.playback_progress.value()==window.playback_progress.maximum()
        window.listen_button.click();assert window.controller.audio.playing and window.controller.audio.position==0
        window.quiet_button.click()
        checks.append('stop resets cursor; natural completion and replay work')

        # Re-export must not bake the 100 ms physical lead-in into editable time.
        window.export_button.click();finish(window);repeated_folder,report=window.controller.state.result
        assert repeated_folder!=edited_folder and window.controller.state.project['notes']==edited_notes
        assert load_project(repeated_folder/'工程.hstudio')['notes']==edited_notes
        assert json.loads((repeated_folder/'音符.json').read_text(encoding='utf-8'))==actual_notes
        assert load_project(home/'autosave.hstudio')['notes']==edited_notes
        checks.append('repeated export preserves canonical timing with no accumulated lead-in')

        window.listen_button.click();window.seek_editor(16.0);window.poll();app.processEvents();assert_centered(window.roll)
        screenshot=report_path.with_suffix('.png');assert window.grab().save(str(screenshot))
        assert window.arm_button.isEnabled() and window.folder_button.isEnabled()
        window.close();app.processEvents();assert window.controller.audio.path is None and not window.controller.audio.playing
        restored=create_window();assert restored.restore_button.isEnabled();restored.restore_button.click()
        assert restored.controller.state.project['notes']==edited_notes and restored.roll.get_notes()==edited_notes
        assert restored.controller.state.source is None and not restored.controller.state.project_dirty
        assert not errors,errors
        saved_notes=deepcopy(restored.controller.state.project['notes'])
        restored.roll.fit_pitches();assert restored.roll.get_notes()==saved_notes
        choose_mode(restored,True);assert restored.compact and restored.edit_toolbar.isHidden()
        assert restored.controller.state.project['notes']==saved_notes and restored.roll.get_notes()==saved_notes
        restored.seek_editor(16.0);app.processEvents();assert_centered(restored.roll)
        compact_screenshot=report_path.with_name(report_path.stem+'-compact.png')
        app.processEvents();assert restored.grab().save(str(compact_screenshot))
        choose_mode(restored,False);assert not restored.compact
        assert restored.controller.state.project['notes']==saved_notes
        checks.append('piano pitch axis and mode changes through Settings preserve the edited score')
        checks.append('full and compact playback stay centered with aligned score coordinates')
        observed=[];settings_screenshot=report_path.with_name(report_path.stem+'-settings.png')
        def cancel_theme():
            dialog=app.activeModalWidget();observed.append(isinstance(dialog,SettingsDialog))
            dialog.theme.setCurrentIndex(dialog.theme.findData('blue'))
            observed.append(restored.roll._theme['accent'].name()==theme_palette('blue')['accent'].lower())
            app.processEvents();assert dialog.grab().save(str(settings_screenshot))
            dialog.reject()
        QTimer.singleShot(30,cancel_theme);restored.open_settings()
        assert observed==[True,True]
        assert restored.roll._theme['accent'].name()==theme_palette('paper')['accent'].lower()
        def save_theme():
            dialog=app.activeModalWidget();dialog.theme.setCurrentIndex(dialog.theme.findData('forest'));dialog.accept()
        QTimer.singleShot(30,save_theme);restored.open_settings()
        assert load_preferences(home/'preferences.json').theme=='forest'
        checks.append('settings preview cancels correctly and theme persists')
        choose_mode(restored,True)
        action=next(a for a in restored.library_actions if a.data()=='春日影.mid');action.trigger();finish(restored)
        assert restored.controller.state.project and len(restored.controller.state.project['notes'])==660
        assert restored.controller.state.result[1]['dropped_out_of_range']==0 and restored.controller.state.result[1]['delayed_notes']==0
        validate_ahk(restored.controller.state.result[0])
        restored.listen_button.click();assert restored.animation_timer.isActive() and restored.animation_timer.interval()==16
        restored.timer.stop();start=restored.playback_progress.value();QTest.qWait(60)
        assert restored.playback_progress.value()>start
        restored.pause_button.click();paused=restored.playback_progress.value();QTest.qWait(50)
        assert restored.playback_progress.value()==paused and not restored.animation_timer.isActive()
        restored.quiet_button.click()
        restored.seek_editor(80.0);assert_centered(restored.roll)
        song_screenshot=report_path.with_name(report_path.stem+'-haruhikage.png')
        app.processEvents();assert restored.grab().save(str(song_screenshot))
        checks.extend(['compact selection automatically converts Haruhikage with no dropped notes',
                       '60 Hz visual transport advances between device polls and freezes on pause'])
        # Exercise the shipped HTTP assets and Qt command bridge; no game input.
        from concurrent.futures import ThreadPoolExecutor
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        from .remote_ui import RemoteDialog
        restored.timer.start(100)
        remote=restored.start_remote(host='127.0.0.1',port=0)
        with ThreadPoolExecutor(max_workers=1) as requests:
            def remote_request(path='/api/state',command=None):
                def fetch():
                    request=Request(f'http://127.0.0.1:{remote.port}'+path,
                        data=None if command is None else json.dumps(command).encode(),
                        headers={'Authorization':'Bearer '+remote.token,'Content-Type':'application/json'})
                    try:
                        with urlopen(request,timeout=5) as response:
                            body=response.read()
                            return json.loads(body) if path.startswith('/api/') else body
                    except HTTPError as response:return json.load(response)
                pending=requests.submit(fetch);deadline=time.monotonic()+10
                while not pending.done():
                    assert time.monotonic()<deadline,'Remote command timed out'
                    app.processEvents();QTest.qWait(10)
                return pending.result()
            for path in ('/','/remote.css','/remote.js'):assert len(remote_request(path))>100
            state=remote_request();assert len(state['library'])==len(entries)
            assert str(home) not in json.dumps(state,ensure_ascii=False)
            phone_score=remote_request('/api/score')
            assert phone_score['id']==state['score_id'] and len(phone_score['notes'])==660
            assert 'notes' not in state
            scheduled=json.loads((restored.controller.state.result[0]/'音符.json').read_text(encoding='utf-8'))
            assert abs(phone_score['notes'][0][0]-scheduled[0]['start'])<.000001
            checks.append('phone score uses exported timing and separate authenticated score snapshots')
            assert remote_request('/api/command',{'action':'play'})['ok'];assert restored.controller.audio.playing
            assert remote_request('/api/command',{'action':'seek','position':12})['ok']
            assert abs(restored.controller.audio.position-12)<.01
            assert remote_request('/api/command',{'action':'pause'})['ok'];assert not restored.controller.audio.playing
            assert remote_request('/api/command',{'action':'stop'})['ok'];assert restored.controller.audio.position==0
            first=state['library'][0]
            assert remote_request('/api/command',{'action':'select','song_id':first['id']})['ok']
            finish(restored);assert restored.controller.state.project and not restored.controller.state.export_dirty
            restored.remote_dialog=RemoteDialog(remote,[('本机验证','127.0.0.1')],restored)
            restored.remote_dialog.show();app.processEvents()
            pairing=restored.remote_dialog;original_url=pairing.url.text()
            pairing.standard.click();assert pairing.qr.pixmap().toImage().pixelColor(0,0).name()=='#ffffff'
            restored.apply_theme('plum');pairing.artistic.click()
            assert pairing.qr.pixmap().toImage().pixelColor(0,0).name()==theme_palette('plum')['surface'].lower()
            assert pairing.url.text()==original_url and remote.active
            checks.append('artistic and standard QR switch with live theme while retaining pairing URL')
            pairing_screenshot=report_path.with_name(report_path.stem+'-pairing.png')
            assert restored.remote_dialog.grab().save(str(pairing_screenshot))
            assert remote.last_client_at is not None
        restored.stop_remote();assert not remote.active
        checks.extend(['authenticated phone library and shipped browser assets',
            'phone play/pause/seek/stop control the desktop audio transport',
            'phone selection prepares a library song and pairing QR renders offline',
            'closing remote stops its listener'])
        # Check the bundled new timing path without touching the user's project.
        from .project import make_project,save_project
        long_notes=[dict(start=0,end=5,pitch=60,velocity=80),dict(start=15,end=16,pitch=62,velocity=90)]
        long_project=home/'长空白验证.hstudio';save_project(long_project,make_project(long_notes,'长空白验证'))
        restored.open_project(long_project);restored.begin_export();finish(restored)
        shortened=restored.controller.state.preview_duration
        assert restored.controller.state.project['notes']==long_notes
        assert restored.controller.state.result[1]['skipped_long_rests']==1
        assert abs(restored.controller.state.result[1]['removed_rest_seconds']-9.4)<.000001
        assert abs(restored.controller.to_audio(4)-4.1)<.000001
        assert abs(restored.controller.to_score(5.4)-10)<.000001
        validate_ahk(restored.controller.state.result[0])
        score=restored.remote_score_snapshot()
        assert abs(score['notes'][1][0]-score['notes'][0][1]-.6)<.000001
        def disable_skip():
            dialog=app.activeModalWidget();dialog.skip_long_rests.setChecked(False);dialog.accept()
        QTimer.singleShot(30,disable_skip);restored.open_settings()
        assert restored.controller.state.result is None and restored.controller.state.export_dirty
        restored.begin_export();finish(restored)
        assert abs(restored.controller.state.preview_duration-shortened-9.4)<.001
        assert restored.controller.state.project['notes']==long_notes and restored.controller.state.result[1]['skipped_long_rests']==0
        assert not load_preferences(home/'preferences.json').skip_long_rests
        checks.extend(['long silence shortens consistently for audio, game script and phone score',
                       'held notes keep normal cursor speed while silent regions pass faster',
                       'disabling long-rest setting invalidates previous exports and restores original timing'])
        from .midi import write_midi
        from .storage import load_options
        from .melody import simplify
        held=[dict(pitch=72,start=0,end=2,velocity=80),dict(pitch=74,start=2,end=2.5,velocity=80)]
        assert simplify(held+[dict(pitch=67,start=.5,end=.8,velocity=80)],'continuous')==held
        wide=home/'乐句八度验证.mid'
        write_midi([dict(pitch=p,start=s,end=s+.5,velocity=80) for p,s in ((36,0),(40,.5),(96,2),(100,2.5))],wide)
        choose_mode(restored,False);restored.load_file(wide);finish(restored)
        restored.mode.setCurrentIndex(restored.mode.findData('continuous'));restored.phrase_octave.setChecked(True)
        restored.tabs.setCurrentIndex(0);app.processEvents()
        assert restored.grab().save(str(report_path.with_name(report_path.stem+'-melody-options.png')))
        restored.convert_button.click();finish(restored)
        assert len(restored.controller.state.project['notes'])==4
        assert restored.controller.state.result[1]['dropped_out_of_range']==0
        assert restored.controller.state.result[1]['phrase_adjusted_notes']>0
        assert load_options(home/'settings.json').melody_mode=='continuous'
        assert load_options(home/'settings.json').phrase_octave
        assert '按句调整' in restored.summary.text() and '半音' in restored.summary.toolTip()
        assert restored.grab().save(str(report_path.with_name(report_path.stem+'-melody-result.png')))
        validate_ahk(restored.controller.state.result[0])
        checks.extend(['continuous extraction preserves sustained melody over lower accompaniment',
                       'phrase octave controls retain wide phrases and persist conversion settings',
                       'phrase changes are visible and exported with playable MIDI and AHK'])
        restored.close();app.processEvents()
        checks.extend(['autosave restores edited score in a new window',
            'rendered current editor and playback cursor','window close releases audio'])
        result=dict(ok=True,report=report,export=str(repeated_folder),project=str(saved),
            screenshot=str(screenshot),compact_screenshot=str(compact_screenshot),song_screenshot=str(song_screenshot),settings_screenshot=str(settings_screenshot),checks=checks);code=0
    except BaseException:
        result=dict(ok=False,error=traceback.format_exc(),ui_errors=errors,checks=checks);code=1
    finally:
        for window in windows:
            try:window.close()
            except BaseException:pass
        if app is not None:app.processEvents()
    report_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return code
