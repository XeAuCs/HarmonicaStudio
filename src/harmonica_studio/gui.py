"""Import, edit canonical notes, preview and export a self-contained project."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from datetime import datetime
import json, logging, threading, uuid
from PySide6.QtCore import Qt,QTimer,QUrl,Signal,QFileSystemWatcher,QPointF,QLineF
from PySide6.QtGui import QDesktopServices,QKeySequence,QShortcut,QIcon,QPainter,QColor,QPen
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QLabel,QPushButton,QFrame,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,
    QDoubleSpinBox,QSpinBox,QCheckBox,QComboBox,QFormLayout,QMessageBox,QProgressBar,
    QAbstractItemView,QTabWidget,QSlider,QMenu,QDialog)
from . import __version__
from .midi import read_midi
from .melody import rank_parts
from .models import Options
from .paths import resource_root,data_root,icon_path,default_library_root
from .library import sample_entries
from .service import convert,export_project
from .project import load_project,save_project,validate_project
from .editor import NoteEditor
from .storage import load_options,save_options
from .playback import ScriptPlayer,AudioPlayer
from .theme import STYLE,make_style,theme_palette
from .preferences import load_preferences,save_preferences
from .settings_ui import SettingsDialog
from .transport import PlaybackClock,TimeMap,playback_anchors
from .remote_control import RemoteControlMixin

def label(text,role=None):
    x=QLabel(text);x.setWordWrap(True)
    if role:x.setObjectName(role)
    return x

def card():
    frame=QFrame();frame.setObjectName('card');box=QVBoxLayout(frame)
    box.setContentsMargins(18,16,18,16);box.setSpacing(12)
    return frame,box

def button(text,slot,primary=False):
    b=QPushButton(text);b.clicked.connect(slot)
    if primary:b.setObjectName('primary')
    return b

class SeekSlider(QSlider):
    seekCommitted=Signal(int)
    def __init__(self):
        super().__init__(Qt.Horizontal);self.setRange(0,0);self.setMinimumHeight(24)
        self._visual_value=0.0;self._colors=theme_palette()
        self.valueChanged.connect(self._changed)
    def _changed(self,value):self._visual_value=float(value);self.update()
    def set_theme(self,palette):self._colors=palette;self.update()
    def set_playback_position(self,seconds):
        if self.isSliderDown():return
        value=max(self.minimum(),min(self.maximum(),seconds*1000))
        self.setValue(round(value));self._visual_value=value;self.update()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.Antialiasing)
        left,right=7.0,max(7.0,self.width()-7.0);y=self.height()/2
        fraction=(self._visual_value-self.minimum())/max(1,self.maximum()-self.minimum())
        x=left+fraction*(right-left)
        painter.setPen(QPen(QColor(self._colors['line']),1.5));painter.drawLine(QLineF(left,y,right,y))
        accent=QColor(self._colors['accent'] if self.isEnabled() else self._colors['muted'])
        painter.setPen(QPen(accent,2));painter.drawLine(QLineF(left,y,x,y))
        painter.setBrush(QColor(self._colors['surface']));painter.setPen(QPen(accent,1.7));painter.drawEllipse(QPointF(x,y),5,5)
        if self.hasFocus():
            painter.setPen(QPen(accent,1,Qt.DotLine));painter.setBrush(Qt.NoBrush);painter.drawRect(self.rect().adjusted(1,1,-2,-2))
    def _position(self,event):
        return round(self.minimum()+max(0,min(1,(event.position().x()-7)/max(1,self.width()-14)))*(self.maximum()-self.minimum()))
    def mousePressEvent(self,event):
        if event.button()==Qt.LeftButton:
            self.setSliderDown(True);self.setValue(self._position(event));event.accept()
        else:super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.isSliderDown():self.setValue(self._position(event));event.accept()
        else:super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if event.button()==Qt.LeftButton and self.isSliderDown():
            self.setValue(self._position(event));self.setSliderDown(False);self.seekCommitted.emit(self.value());event.accept()
        else:super().mouseReleaseEvent(event)
    def keyPressEvent(self,event):
        super().keyPressEvent(event);self.seekCommitted.emit(self.value())

def map_time(value,anchors):
    """Translate between editable score time and scheduled physical audio time."""
    return TimeMap(anchors)(value)

class MainWindow(RemoteControlMixin,QMainWindow):
    def __init__(self,home=None,audio=None):
        super().__init__()
        self.home=Path(home or data_root());self.home.mkdir(parents=True,exist_ok=True)
        self.preferences=load_preferences(self.home/'preferences.json');self.compact=False
        self._full_size=None;self._compact_size=None;self._duration_notes=None;self._score_duration=0
        self.visual_clock=PlaybackClock()
        self._remote_prepare=False;self._remote_autoplay=False;self._remote_job=False;self._remote_dispatching=False
        self.source=None;self.parts={};self.names={};self.keys=[];self.result=None;self.sample_part=None
        self.project=None;self.project_path=None;self.project_dirty=False;self.export_dirty=False
        self.logical_seek=0.0;self.preview_duration=0.0;self.transport='ready';self.time_anchors=[]
        self.audio=audio or AudioPlayer();self.player=ScriptPlayer(self.home/'control')
        self.executor=ThreadPoolExecutor(max_workers=1);self.future=None;self.job_kind=None;self.after_export=None;self.cancel=threading.Event()
        self.setWindowTitle('口琴工坊 · Harmonica Studio');self.resize(1220,880);self.setMinimumSize(1080,760);self.setAcceptDrops(True)
        self.setWindowIcon(QIcon(str(icon_path())))
        root=QWidget();root.setObjectName('root');self.setCentralWidget(root)
        layout=QVBoxLayout(root);layout.setContentsMargins(28,22,28,18);layout.setSpacing(16)
        header=QHBoxLayout();header.setSpacing(16);logo=QLabel();logo.setPixmap(self.windowIcon().pixmap(44,44));logo.setFixedSize(46,46);header.addWidget(logo)
        titlebox=QVBoxLayout();titlebox.setSpacing(3);titlebox.addWidget(label('口琴工坊','brand'))
        titlebox.addWidget(label('HARMONICA  /  曲谱与演奏','eyebrow'));header.addLayout(titlebox,1)
        self.remote_button=button('手机遥控',self.open_remote);header.addWidget(self.remote_button)
        self.settings_button=button('设置',self.open_settings);header.addWidget(self.settings_button)
        header.addWidget(label('v'+__version__,'muted'));layout.addLayout(header)
        filecard,filebox=card();filerow=QHBoxLayout()
        self.filename=label('选择一首曲谱','section');filerow.addWidget(self.filename,1)
        self.example_button=QPushButton('曲库');self.sample_menu=QMenu(self.example_button);self.sample_menu.setToolTipsVisible(True)
        self.sample_menu.aboutToShow.connect(self.refresh_library)
        self.example_button.setMenu(self.sample_menu);self.open_button=button('打开 MIDI',self.choose_file,True)
        self.project_button=button('打开工程',self.choose_project);self.restore_button=button('恢复上次编辑',self.restore_last)
        for b in (self.example_button,self.open_button,self.project_button,self.restore_button):filerow.addWidget(b)
        filebox.addLayout(filerow);layout.addWidget(filecard)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        import_page=QWidget();importbox=QHBoxLayout(import_page);importbox.setContentsMargins(0,12,0,0);importbox.setSpacing(14)
        left,leftbox=card();leftbox.addWidget(label('声部','section'))
        self.filehint=label('支持 MIDI / KAR / RMID。带 ★ 的是推荐声部，已排除打击乐。','muted');leftbox.addWidget(self.filehint)
        self.table=QTableWidget(0,4);self.table.setHorizontalHeaderLabels(['音轨 / 声部','音符','音域','时长'])
        self.table.verticalHeader().hide();self.table.setAlternatingRowColors(True);self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection);self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        for i in range(1,4):self.table.horizontalHeader().setSectionResizeMode(i,QHeaderView.ResizeToContents)
        leftbox.addWidget(self.table,1);importbox.addWidget(left,3)
        right,rightbox=card();rightbox.addWidget(label('调音','section'));form=QFormLayout();form.setSpacing(12)
        self.speed=QDoubleSpinBox();self.speed.setRange(.25,2);self.speed.setSingleStep(.05);self.speed.setSuffix(' 倍')
        self.transpose=QSpinBox();self.transpose.setRange(-24,24);self.transpose.setSuffix(' 半音')
        self.mode=QComboBox();self.mode.addItem('连贯旋律（减少伴奏穿插）','sustain');self.mode.addItem('同刻最高音','highest')
        form.addRow('播放速度',self.speed);form.addRow('整体移调',self.transpose);form.addRow('提取方式',self.mode);rightbox.addLayout(form)
        self.octave=QCheckBox('自动调整八度，适配口琴音域');self.trim=QCheckBox('去掉开头的空白等待')
        rightbox.addWidget(self.octave);rightbox.addWidget(self.trim)
        old=load_options(self.home/'settings.json');self.speed.setValue(old.speed);self.transpose.setValue(old.transpose)
        self.octave.setChecked(old.auto_octave);self.trim.setChecked(old.trim_silence);self.mode.setCurrentIndex(0 if old.melody_mode=='sustain' else 1)
        rightbox.addWidget(label('参数用于重新提取旋律，生成后可在音符图中继续修改。','muted'));rightbox.addStretch()
        self.convert_button=button('生成曲谱',self.begin_convert,True);rightbox.addWidget(self.convert_button)
        importbox.addWidget(right,2);self.tabs.addTab(import_page,'曲谱')
        editor_page=QWidget();editorbox=QVBoxLayout(editor_page);editorbox.setContentsMargins(0,12,0,0)
        editcard,editbox=card();editorbox.addWidget(editcard,1)
        top=QHBoxLayout();self.summary=label('旋律','result');top.addWidget(self.summary,1)
        self.save_button=button('保存工程',self.save_current);top.addWidget(self.save_button);editbox.addLayout(top)
        self.edit_toolbar=QWidget();toolsrow=QHBoxLayout(self.edit_toolbar);toolsrow.setContentsMargins(0,0,0,0)
        self.undo_button=button('撤销',lambda:self.roll.undo());self.redo_button=button('重做',lambda:self.roll.redo())
        self.delete_button=button('删除音符',lambda:self.roll.delete_selected())
        for b in (self.undo_button,self.redo_button,self.delete_button):toolsrow.addWidget(b)
        toolsrow.addStretch()
        for text,callback in [('音域 −',lambda:self.roll.pitch_zoom_out()),('音域 +',lambda:self.roll.pitch_zoom_in()),('适配音域',lambda:self.roll.fit_pitches()),('时间 −',lambda:self.roll.zoom_out()),('时间 +',lambda:self.roll.zoom_in())]:
            toolsrow.addWidget(button(text,callback))
        editbox.addWidget(self.edit_toolbar)
        self.roll=NoteEditor();self.roll.setMinimumHeight(250)
        self.roll.notesChanged.connect(self.notes_changed);self.roll.seekRequested.connect(self.seek_editor)
        self.roll.message.connect(lambda text:self.status.setText(text));self.roll.historyChanged.connect(lambda *_:self.refresh_controls())
        editbox.addWidget(self.roll,1)
        self.edit_hint=label('拖动修改音符 · 拉右边缘改时长 · 双击空白添加 · 点击时间尺定位试听','muted');editbox.addWidget(self.edit_hint)
        timeline=QHBoxLayout();self.playback_state=label('未播放','muted');self.playback_state.setMinimumWidth(55)
        self.playback_progress=SeekSlider();self.playback_progress.setAccessibleName('试听播放进度，可拖动跳转')
        self.playback_progress.seekCommitted.connect(lambda ms:self.seek_audio(ms/1000))
        self.playback_time=label('00:00 / 00:00','muted');self.playback_time.setMinimumWidth(120);self.playback_time.setAlignment(Qt.AlignRight|Qt.AlignVCenter)
        timeline.addWidget(self.playback_state);timeline.addWidget(self.playback_progress,1);timeline.addWidget(self.playback_time);editbox.addLayout(timeline)
        row=QHBoxLayout();self.listen_button=button('试听',self.listen,True);self.pause_button=button('暂停',self.pause_listening)
        self.quiet_button=button('停止',self.stop_listening);self.export_button=button('导出修改',self.begin_export)
        self.folder_button=button('导出文件',self.open_export);self.arm_button=button('演奏',self.arm);self.stop_button=button('结束演奏',self.stop_script)
        for b in (self.listen_button,self.pause_button,self.quiet_button,self.export_button,self.folder_button,self.arm_button,self.stop_button):row.addWidget(b)
        editbox.addLayout(row)
        self.tabs.addTab(editor_page,'编辑与试听')
        bottom=QHBoxLayout();self.status=label('从曲库选歌，或拖入 MIDI。','muted');bottom.addWidget(self.status,1)
        self.cancel_button=button('取消转换',self.cancel_job);bottom.addWidget(self.cancel_button);layout.addLayout(bottom)
        self.progress=QProgressBar();self.progress.setRange(0,1);self.progress.setValue(0);self.progress.setTextVisible(False);layout.addWidget(self.progress)
        self.footer=label('试听为合成音色  /  F6 开始、停止演奏 · F8 退出  /  游戏兼容性尚未实测','muted');layout.addWidget(self.footer)
        self.autosave_timer=QTimer(self);self.autosave_timer.setSingleShot(True);self.autosave_timer.timeout.connect(self.autosave)
        QShortcut(QKeySequence.Save,self,activated=self.save_current)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(100)
        self.animation_timer=QTimer(self);self.animation_timer.setTimerType(Qt.PreciseTimer);self.animation_timer.timeout.connect(self.animate_playback)
        self.library_timer=QTimer(self);self.library_timer.setSingleShot(True);self.library_timer.timeout.connect(self.refresh_library)
        self.library_watcher=QFileSystemWatcher(self);self.library_watcher.directoryChanged.connect(lambda _:self.library_timer.start(200))
        self.refresh_library();self.apply_theme(self.preferences.theme);self.set_compact(self.preferences.compact,initial=True);self.refresh_controls()
        self.setup_remote()

    @property
    def time_anchors(self):return self._time_anchors
    @time_anchors.setter
    def time_anchors(self,value):
        self._time_anchors=list(value);self._to_audio=TimeMap(value);self._to_score=TimeMap((b,a) for a,b in value)

    def library_root(self):
        return Path(self.preferences.library_folder) if self.preferences.library_folder else default_library_root()

    def refresh_library(self):
        folder=self.library_root();entries=sample_entries(folder);self.sample_menu.clear();self.library_actions=[]
        self._library_entries=entries
        for entry in entries:
            text=entry['title'];duration=entry.get('duration_seconds')
            if type(duration) in (int,float):text+=f'  /  {duration:.0f} 秒'
            action=self.sample_menu.addAction(text);action.setData(entry['file'])
            action.setToolTip(str(entry.get('description',''))+('\n参考时长按 1 倍速计算。' if duration is not None else '')+'\n'+str(entry['path']))
            action.triggered.connect(lambda checked=False,item=entry:self.load_file(item['path'],item.get('options')))
            self.library_actions.append(action)
        if not entries:
            action=self.sample_menu.addAction('文件夹中还没有 MIDI' if folder.is_dir() else '曲库文件夹不存在');action.setEnabled(False)
        self.sample_menu.addSeparator()
        self.sample_menu.addAction('打开曲库文件夹',self.open_library_folder)
        self.sample_menu.addAction('刷新曲库',self.refresh_library)
        self.sample_menu.addAction('曲库设置…',self.open_settings)
        self.example_button.setText(f'曲库 · {len(entries)}')
        self.example_button.setToolTip('自动扫描：'+str(folder))
        watched=self.library_watcher.directories()
        target=folder.resolve()
        while not target.is_dir() and target.parent!=target:target=target.parent
        desired=str(target)
        if watched!=[desired]:
            if watched:self.library_watcher.removePaths(watched)
            if target.is_dir():self.library_watcher.addPath(desired)

    def open_library_folder(self):
        folder=self.library_root()
        try:
            folder.mkdir(parents=True,exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)));self.refresh_library()
        except OSError as exc:self.show_error(exc)

    def apply_theme(self,name):
        self.setStyleSheet(make_style(name));palette=theme_palette(name)
        self.roll.set_theme(palette);self.playback_progress.set_theme(palette)
        if getattr(self,'remote_dialog',None):self.remote_dialog.set_theme(palette)

    def open_settings(self):
        dialog=SettingsDialog(self.preferences,self.library_root(),busy=self.future is not None,parent=self)
        dialog.themePreview.connect(self.apply_theme)
        if dialog.exec()==QDialog.Accepted:
            try:
                preferences=dialog.preferences();save_preferences(self.home/'preferences.json',preferences)
                timing_changed=preferences.skip_long_rests!=self.preferences.skip_long_rests
                self.preferences=preferences
                if timing_changed and self.project is not None:
                    position=self.logical_seek
                    self.reset_result();self.logical_seek=position
                    self.project.setdefault('options',{})['skip_long_rests']=preferences.skip_long_rests
                    self.export_dirty=True;self.project_dirty=True
                    self.display_position(position,'待生成')
                    self.update_summary();self.autosave_timer.start(400)
                    self.status.setText('播放设置已保存，下次试听或游戏演奏将使用新的停顿时长。')
                self.apply_theme(preferences.theme);self.refresh_library();self.set_compact(preferences.compact)
            except Exception as exc:self.apply_theme(self.preferences.theme);self.show_error(exc)
        else:self.apply_theme(self.preferences.theme)
        dialog.deleteLater()

    def set_compact(self,compact,initial=False):
        compact=bool(compact);changed=compact!=self.compact
        if changed:
            if compact:self._full_size=self.size()
            else:self._compact_size=self.size()
        self.compact=compact
        self.tabs.setTabVisible(0,not compact);self.tabs.tabBar().setVisible(not compact)
        if compact:self.tabs.setCurrentIndex(1)
        for widget in (self.edit_toolbar,self.edit_hint,self.save_button,self.export_button,self.restore_button,self.project_button,self.folder_button):widget.setVisible(not compact)
        self.roll.setMinimumHeight(170 if compact else 250);self.roll.set_compact(compact)
        self.tabs.setMinimumHeight(300 if compact else 495)
        self.setMinimumSize(800,590) if compact else self.setMinimumSize(1080,820)
        if changed:
            size=self._compact_size if compact else self._full_size
            if size:self.resize(size)
            elif compact:self.resize(900,600)
        self.refresh_controls()
        if compact and not initial and self.parts and self.project is None and not self.future:self.begin_convert()
        if not self.project:self.summary.setText('从曲库选歌，自动生成旋律。' if compact else '生成曲谱后，在这里编辑旋律。')

    def refresh_controls(self):
        busy=self.future is not None;playing=self.transport=='playing';has=self.project is not None
        if hasattr(self,'animation_timer'):
            if playing and not self.animation_timer.isActive():self.animation_timer.start(16)
            elif not playing and self.animation_timer.isActive():self.animation_timer.stop()
        has_notes=has and bool(self.project['notes']);current=self.result is not None and not self.export_dirty
        for w in (self.open_button,self.example_button,self.project_button,self.table,self.speed,self.transpose,self.mode,self.octave,self.trim):w.setEnabled(not busy)
        self.restore_button.setEnabled(not busy and (self.home/'autosave.hstudio').is_file())
        self.convert_button.setEnabled(not busy and bool(self.parts));self.cancel_button.setEnabled(busy and self.job_kind in ('convert','export'))
        self.cancel_button.setVisible(busy);self.progress.setVisible(busy)
        self.save_button.setEnabled(has and not busy);self.export_button.setEnabled(has_notes and not busy)
        retry=self.compact and bool(self.parts) and self.project is None
        self.listen_button.setEnabled((has_notes or retry) and not busy and not playing);self.pause_button.setEnabled(playing)
        self.listen_button.setText('重新生成' if retry else '继续试听' if self.transport=='paused' else '试听')
        self.quiet_button.setEnabled(has and not busy);self.playback_progress.setEnabled(has_notes and not busy)
        self.folder_button.setEnabled(current and not busy);self.arm_button.setEnabled(current and not busy and not self.player.alive);self.stop_button.setEnabled(self.player.alive)
        self.roll.set_read_only(busy or playing or not has or self.compact)
        self.undo_button.setEnabled(has and not busy and not playing and self.roll.can_undo);self.redo_button.setEnabled(has and not busy and not playing and self.roll.can_redo)
        self.delete_button.setEnabled(has and not busy and not playing and not self.compact);self.progress.setRange(0,0 if busy else 1)
        if not busy:self.progress.setValue(1 if self.result else 0)
        self.setWindowTitle(('● ' if self.project_dirty else '')+'口琴工坊 · Harmonica Studio')

    def cancel_job(self):self.cancel.set()
    def choose_file(self):
        path,_=QFileDialog.getOpenFileName(self,'打开 MIDI 曲谱','', 'MIDI 曲谱 (*.mid *.midi *.kar *.rmi)')
        if path:self.load_file(path)
    def choose_project(self):
        path,_=QFileDialog.getOpenFileName(self,'打开口琴工程','', '口琴工程 (*.hstudio)')
        if path:self.open_project(path)
    def load_example(self):self.load_file(resource_root()/'samples/欢乐颂.mid')
    def restore_last(self):self.open_project(self.home/'autosave.hstudio')

    def preserve_current(self):
        if self.project is not None and self.project_dirty:
            path=self.home/'recovery'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.hstudio');save_project(path,self.project)
            save_project(self.home/'autosave.hstudio',self.project)

    def reset_result(self):
        self.stop_listening();self.audio.close();self.player.stop();self.result=None;self.preview_duration=0;self.time_anchors=[];self.logical_seek=0

    def load_file(self,path,sample_options=None,*,prepare=False,autoplay=False):
        if self.future:return
        self._remote_prepare=prepare;self._remote_autoplay=autoplay;self._remote_job=prepare
        try:
            self.preserve_current();self.reset_result();self.project=None;self.project_path=None;self.project_dirty=False
            self.autosave_timer.stop();self.parts={};self.table.setRowCount(0);self.roll.set_notes([])
            self.sample_part=None
            if isinstance(sample_options,dict):
                track,channel=sample_options.get('track'),sample_options.get('channel')
                if type(track) is int and type(channel) is int:self.sample_part=(track,channel)
            self.source=Path(path).resolve();self.filename.setText(self.source.stem);self.tabs.setCurrentIndex(1 if self.compact else 0)
            self.job_kind='load';self.future=self.executor.submit(read_midi,self.source);self.status.setText('正在读取曲谱…');self.refresh_controls()
        except Exception as exc:self.show_error(exc)

    def install_parts(self,parts,names):
        self.parts,self.names=parts,names;self.keys=[k for k,_ in rank_parts(parts,names)];self.table.setRowCount(len(self.keys))
        preset=self.sample_part in self.keys;selected=self.keys.index(self.sample_part) if preset else 0
        self.sample_part=None
        def pitch(p):return ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'][p%12]+str(p//12-1)
        for row,key in enumerate(self.keys):
            notes=parts[key];duration=max(n['end'] for n in notes)-min(n['start'] for n in notes)
            values=[('★ ' if row==selected else '')+f'{names.get(key[0],"未命名")} · 轨 {key[0]} / 通道 {key[1]+1}',str(len(notes)),pitch(min(n['pitch'] for n in notes))+' – '+pitch(max(n['pitch'] for n in notes)),f'{duration:.1f} 秒']
            for col,value in enumerate(values):self.table.setItem(row,col,QTableWidgetItem(value))
        self.table.selectRow(selected);self.filehint.setText(f'{len(parts)} 个声部 · 已排除打击乐 · 原文件保持不变')
        self.status.setText('已选中示例预设的旋律声部，也可手动切换。' if preset else '已推荐主旋律。生成后可直接在图上修谱。')

    def selected_options(self):
        row=self.table.currentRow()
        if row<0 or row>=len(self.keys):raise ValueError('请先选择一个声部。')
        track,channel=self.keys[row]
        if self.compact:return Options(track=track,channel=channel,skip_long_rests=self.preferences.skip_long_rests)
        return Options(speed=self.speed.value(),transpose=self.transpose.value(),auto_octave=self.octave.isChecked(),trim_silence=self.trim.isChecked(),melody_mode=self.mode.currentData(),track=track,channel=channel,skip_long_rests=self.preferences.skip_long_rests)

    def begin_convert(self):
        if self.future or not self.source:return
        try:
            options=self.selected_options()
            if not self.compact:save_options(self.home/'settings.json',options)
            self.preserve_current();self.reset_result()
            self.cancel=threading.Event();self.job_kind='convert';self.after_export=None
            self.future=self.executor.submit(convert,self.source,self.home/'exports',options,self.cancel);self.status.setText('正在提取旋律并制作试听…');self.refresh_controls()
        except Exception as exc:self.show_error(exc)

    def open_project(self,path):
        if self.future:return
        try:
            project=load_project(path)
            project.setdefault('options',{})['skip_long_rests']=self.preferences.skip_long_rests
            self.preserve_current();self.reset_result();self.autosave_timer.stop()
            self.project=project;self.project_path=Path(path).resolve();self.project_dirty=False;self.export_dirty=True
            self.source=None;self.parts={};self.table.setRowCount(0);self.roll.set_notes(project['notes'])
            self.filename.setText(project['title']);self.tabs.setCurrentIndex(1);self.update_summary();self.display_position(0,'未播放',False)
            self.status.setText('工程已打开。音符可继续编辑；点试听会生成最新结果。');self.refresh_controls()
        except Exception as exc:self.show_error(exc)

    def save_current(self):
        if self.project is None or self.future:return
        path,_=QFileDialog.getSaveFileName(self,'保存口琴工程',str(self.project_path or self.home/'曲谱.hstudio'),'口琴工程 (*.hstudio)')
        if path:
            try:self.save_to(path)
            except Exception as exc:self.show_error(exc)

    def save_to(self,path):
        path=Path(path)
        if path.suffix.lower()!='.hstudio':path=path.with_suffix('.hstudio')
        save_project(path,self.project);self.project_path=path.resolve();self.project_dirty=False;self.autosave();self.status.setText('工程已保存：'+str(path));self.refresh_controls()

    def autosave(self):
        if self.project is None:return
        try:save_project(self.home/'autosave.hstudio',self.project)
        except Exception:logging.exception('Autosave failed');self.status.setText('自动暂存失败，请点击保存工程选择其他位置。')
        self.refresh_controls()

    def notes_changed(self,notes):
        if self.project is None:return
        self.project=dict(self.project,notes=deepcopy(notes));self.project_dirty=True;self.export_dirty=True
        self.preview_duration=0;self.time_anchors=[];self.transport='ready';self.logical_seek=0;self.visual_clock.reset()
        try:self.audio.close();self.player.stop()
        except Exception:logging.exception('Could not stop previous playback after edit')
        self.display_position(0,'待生成',False);self.update_summary()
        self.status.setText('修改已暂存。点试听即可听到修改后的旋律。');self.autosave_timer.start(400);self.refresh_controls()

    def update_summary(self):
        if self.project is None:return
        removed=self.project.get('report',{}).get('removed_rest_seconds',0) if not self.export_dirty else 0
        rest_info=f'  /  已缩短 {removed:.1f} 秒空白' if isinstance(removed,(int,float)) and removed>0 else ''
        self.summary.setText(f"{len(self.project['notes'])} 音  /  {self.logical_duration():.1f} 秒"+rest_info+('  /  待生成' if self.export_dirty else ''))

    def begin_export(self,checked=False,*,play_after=False):
        if self.future or self.project is None:return
        try:
            snapshot=validate_project(self.project)
            snapshot.setdefault('options',{})['skip_long_rests']=self.preferences.skip_long_rests
            if not snapshot['notes']:raise ValueError('请先双击音符图添加音符。')
            self.audio.stop();self.transport='ready';self.player.stop();self.cancel=threading.Event();self.job_kind='export';self.after_export='play' if play_after else None
            self.future=self.executor.submit(export_project,snapshot,self.home/'exports',self.cancel);self.status.setText('正在为修改后的旋律生成试听、MIDI 和脚本…');self.refresh_controls()
        except Exception as exc:self.show_error(exc)

    def show_result(self,folder,report,replace_project=False):
        folder=Path(folder);new_project=load_project(folder/'工程.hstudio');self.result=(folder,report);self.project=new_project;self.export_dirty=False
        if replace_project:self.roll.set_notes(new_project['notes']);self.project_path=None;self.project_dirty=True;self.logical_seek=0
        actual=json.loads((folder/'音符.json').read_text(encoding='utf-8'))
        self.time_anchors=playback_anchors(self.project['notes'],actual)
        self.filename.setText(new_project['title']);self.tabs.setCurrentIndex(1);self.update_summary();self.autosave()
        self.load_preview();self.status.setText('曲谱已准备好，可以试听或演奏。' if self.compact else f"已生成 {report['melody_notes']} 个音符，可继续编辑。")

    def load_preview(self):
        self.preview_duration=0;self.transport='ready'
        self.audio.load(self.result[0]/'试听.wav');self.preview_duration=self.audio.duration
        self.seek_editor(self.logical_seek)

    def poll(self):
        try:self.update_playback();self.player.reap()
        except Exception as exc:self.transport='ready';self.show_error(exc)
        if self.future and self.future.done():
            future,self.future=self.future,None
            completed_kind=self.job_kind
            try:
                result=future.result()
                if self.job_kind=='load':
                    self.install_parts(*result)
                    if self.compact or self._remote_prepare:self.begin_convert()
                else:
                    self.show_result(*result,replace_project=self.job_kind=='convert')
                    if self._remote_job:self._remote_message='曲谱已就绪。'
                    if self.after_export=='play' or self._remote_autoplay:self.listen()
                    elif self.after_export=='game':self.remote_game_play()
            except InterruptedError:self.status.setText('已取消；可以继续编辑或重新导出。')
            except Exception as exc:self.show_error(exc)
            self.after_export=None
            if completed_kind!='load' or not self.future:
                self._remote_prepare=False;self._remote_autoplay=False;self._remote_job=False
        self.refresh_controls()

    def logical_duration(self):
        notes=self.project['notes'] if self.project else None
        if notes is not self._duration_notes:
            self._duration_notes=notes;self._score_duration=max((n['end'] for n in notes),default=0) if notes else 0
        return self._score_duration
    def display_position(self,position,state,show_cursor=True):
        total=self.preview_duration or self.logical_duration();position=max(0,min(total,position))
        def stamp(t):return f'{int(t)//60:02d}:{int(t)%60:02d}'
        self.playback_progress.setRange(0,max(0,round(total*1000)))
        self.playback_progress.set_playback_position(position)
        text=f'{stamp(position)} / {stamp(total)}'
        if self.playback_time.text()!=text:self.playback_time.setText(text)
        if self.playback_state.text()!=state:self.playback_state.setText(state)
        logical=self._to_score(position) if self.preview_duration else position
        self.roll.set_position(logical if show_cursor else None,self.logical_duration())

    def seek_editor(self,seconds):
        self.logical_seek=max(0,min(self.logical_duration(),seconds));self.seek_audio(self._to_audio(self.logical_seek) if self.preview_duration else self.logical_seek)
    def seek_audio(self,seconds):
        try:
            if self.preview_duration:
                resume=self.transport=='playing';self.audio.seek(seconds,resume=resume)
                position=self.audio.position;self.visual_clock.reset(position,resume)
                self.logical_seek=self._to_score(position);self.display_position(position,'试听中' if resume else '已定位')
                if not resume:self.transport='ready'
            else:self.logical_seek=max(0,min(self.logical_duration(),seconds));self.display_position(self.logical_seek,'已定位')
        except Exception as exc:self.show_error(exc)

    def listen(self):
        if self.future:return
        if self.project is None:
            if self.compact and self.parts:self.begin_convert()
            return
        if self.export_dirty or not self.result:self.begin_export(play_after=True);return
        try:
            self.player.stop()
            if not self.preview_duration:self.load_preview()
            start=self.audio.position
            if self.transport=='ended' or start>=self.preview_duration-.01:start=0;self.logical_seek=0
            self.audio.play(start);self.transport='playing';self.visual_clock.reset(start,True);self.display_position(start,'试听中');self.refresh_controls()
        except Exception as exc:self.show_error(exc)
    def pause_listening(self):
        try:
            self.audio.pause();self.transport='paused';position=self.audio.position;self.visual_clock.reset(position)
            self.logical_seek=self._to_score(position);self.display_position(position,'已暂停');self.refresh_controls()
        except Exception as exc:self.show_error(exc)
    def stop_listening(self):
        self.audio.stop();self.transport='ready';self.visual_clock.reset();self.logical_seek=0
        self.display_position(0,'已停止' if self.project else '未播放',False);self.roll.reset_timeline();self.refresh_controls()
    def update_playback(self):
        if self.transport!='playing':return
        position=self.audio.position;playing=self.audio.playing;self.logical_seek=self._to_score(position)
        self.visual_clock.synchronize(position,playing)
        if not playing:self.transport='ended'
        self.display_position(self.visual_clock.position(duration=self.preview_duration) if playing else self.preview_duration,'试听中' if playing else '已结束')

    def animate_playback(self):
        if self.transport=='playing' and not self.playback_progress.isSliderDown():
            self.display_position(self.visual_clock.position(duration=self.preview_duration),'试听中')

    def open_export(self):
        if self.result and not self.export_dirty:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result[0])))
    def arm(self):
        if self.result and not self.export_dirty:
            try:self.stop_listening();self.player.start(self.result[0]/'演奏脚本.ahk');self.status.setText('演奏器已就绪；切到口琴界面按 F6，3 秒后开始。F8 退出。')
            except Exception as exc:self.show_error(exc)
    def stop_script(self):self.player.stop();self.status.setText('已请求停止；演奏器会松开按键后退出。')
    def show_error(self,exc):
        logging.error('Operation failed: %s',exc,exc_info=True);self.status.setText('未完成：'+str(exc))
        if self._remote_dispatching or self._remote_job:
            self._remote_error=True;self._remote_message='操作未完成，请检查电脑端提示。'
        else:QMessageBox.warning(self,'需要处理',str(exc))
    def dragEnterEvent(self,event):
        if not self.future and event.mimeData().hasUrls() and any(u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in ('.mid','.midi','.rmi','.kar','.hstudio') for u in event.mimeData().urls()):event.acceptProposedAction()
    def dropEvent(self,event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path=Path(url.toLocalFile())
                if path.suffix.lower()=='.hstudio':self.open_project(path);break
                if path.suffix.lower() in ('.mid','.midi','.rmi','.kar'):self.load_file(path);break
    def closeEvent(self,event):
        try:
            if self.project is not None:save_project(self.home/'autosave.hstudio',self.project)
        except Exception as exc:self.show_error(exc);event.ignore();return
        self.stop_remote()
        self.cancel.set();self.autosave_timer.stop();self.timer.stop();self.animation_timer.stop();self.library_timer.stop()
        self.library_watcher.blockSignals(True)
        if self.library_watcher.directories():self.library_watcher.removePaths(self.library_watcher.directories())
        self.audio.close();self.player.stop();self.executor.shutdown(wait=False,cancel_futures=True);event.accept()

def run(open_remote=False):
    # A stable Windows identity allows the taskbar to display our application icon.
    import sys
    if sys.platform=='win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('HarmonicaStudio.Desktop')
        except (AttributeError,OSError):logging.warning('Could not set taskbar identity')
    app=QApplication.instance() or QApplication([]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
    app.setWindowIcon(QIcon(str(icon_path())))
    window=MainWindow();window.show()
    if open_remote:QTimer.singleShot(300,window.open_remote)
    return app.exec()
