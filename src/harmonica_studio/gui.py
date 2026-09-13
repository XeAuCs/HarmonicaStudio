"""Import, edit canonical notes, preview and export a self-contained project."""
from pathlib import Path
import logging
from PySide6.QtCore import Qt,QTimer,QUrl,Signal,QFileSystemWatcher,QPointF,QLineF
from PySide6.QtGui import QDesktopServices,QKeySequence,QShortcut,QIcon,QPainter,QColor,QPen
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,
    QLabel,QPushButton,QFrame,QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,
    QDoubleSpinBox,QSpinBox,QCheckBox,QComboBox,QFormLayout,QMessageBox,QProgressBar,
    QAbstractItemView,QTabWidget,QSlider,QMenu,QDialog)
from . import __version__
from .models import Options
from .paths import resource_root,data_root,icon_path,default_library_root
from .editor import NoteEditor
from .storage import load_options
from .theme import STYLE,make_style,theme_palette
from .settings_ui import SettingsDialog
from .transport import TimeMap
from .remote_control import RemoteControl
from .remote_ui import RemoteDialog, lan_addresses
from .controller import AppController
from .app_state import FollowUp
from .presenter import DesktopPresenter

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

class MainWindow(QMainWindow):
    def __init__(self,home=None,audio=None,controller=None):
        super().__init__()
        self.controller = controller if controller is not None else AppController(home, audio=audio)
        self.compact = False
        self._full_size = self._compact_size = None
        self.remote_dialog = None
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
        self.mode=QComboBox();self.mode.addItem('长音保护（原方式）','sustain');self.mode.addItem('同刻最高音','highest');self.mode.addItem('连续旋律（兼顾前后音）','continuous')
        self.mode.setToolTip('连续旋律会比较前后音的衔接和长音保持，减少伴奏穿插；不同编曲请对比试听。')
        form.addRow('播放速度',self.speed);form.addRow('整体移调',self.transpose);form.addRow('提取方式',self.mode);rightbox.addLayout(form)
        self.octave=QCheckBox('自动调整八度，适配口琴音域');self.trim=QCheckBox('去掉开头的空白等待')
        self.phrase_octave=QCheckBox('超出音域时，允许按乐句调整八度')
        self.phrase_octave.setToolTip('按明显休止划分乐句，整句升降八度。可能改变乐句间的高低关系；仍无法容纳的音符会列入丢音统计。')
        rightbox.addWidget(self.octave);rightbox.addWidget(self.phrase_octave);rightbox.addWidget(self.trim)
        old=load_options(self.controller.home/'settings.json');self.speed.setValue(old.speed);self.transpose.setValue(old.transpose)
        self.octave.setChecked(old.auto_octave);self.trim.setChecked(old.trim_silence);self.mode.setCurrentIndex(self.mode.findData(old.melody_mode));self.phrase_octave.setChecked(old.phrase_octave)
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
        self.presenter = DesktopPresenter(self, self.controller)
        self.remote_control = RemoteControl(self.controller, blocked=lambda: QApplication.activeModalWidget() is not None)
        self.remote_timer = QTimer(self)
        self.remote_timer.timeout.connect(self.poll_remote)
        self.refresh_library();self.apply_theme(self.controller.preferences.theme);self.set_compact(self.controller.preferences.compact,initial=True);self.refresh_controls()


    def library_root(self):
        return self.controller.library_root()

    def refresh_library(self):
        self.controller.refresh_library()

    def render_library(self):
        folder=self.library_root();entries=self.controller.library;self.sample_menu.clear();self.library_actions=[]
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
        dialog = SettingsDialog(self.controller.preferences, self.library_root(), busy=self.controller.busy, parent=self)
        dialog.themePreview.connect(self.apply_theme)
        if dialog.exec() == QDialog.Accepted:
            try:
                self.controller.update_preferences(dialog.preferences())
                self.apply_theme(self.controller.preferences.theme)
                self.refresh_library()
                self.set_compact(self.controller.preferences.compact)
            except Exception as exc:
                self.apply_theme(self.controller.preferences.theme)
                self.show_error(exc)
        else:
            self.apply_theme(self.controller.preferences.theme)
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
        if compact and not initial and self.controller.state.parts and self.controller.state.project is None and not self.controller.jobs.current:self.begin_convert()
        if not self.controller.state.project:self.summary.setText('从曲库选歌，自动生成旋律。' if compact else '生成曲谱后，在这里编辑旋律。')

    def refresh_controls(self):
        c, s = self.controller, self.controller.state
        caps = c.capabilities()
        busy, playing, has = c.busy, s.transport == 'playing', s.project is not None
        if playing and not self.animation_timer.isActive():
            self.animation_timer.start(16)
        elif not playing and self.animation_timer.isActive():
            self.animation_timer.stop()
        for widget in (self.open_button, self.example_button, self.project_button, self.table,
                       self.speed, self.transpose, self.mode, self.octave, self.phrase_octave, self.trim):
            widget.setEnabled(not busy)
        self.restore_button.setEnabled(not busy and (c.home / 'autosave.hstudio').is_file())
        self.convert_button.setEnabled(caps['can_convert'])
        self.cancel_button.setEnabled(busy and c.jobs.current.kind in ('convert', 'export'))
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.save_button.setEnabled(caps['can_save'])
        self.export_button.setEnabled(caps['can_export'])
        retry = self.compact and bool(s.parts) and s.project is None
        self.listen_button.setEnabled((caps['can_play'] or (retry and not busy)) and not playing)
        self.listen_button.setText('重新生成' if retry else '继续试听' if s.transport == 'paused' else '试听')
        self.pause_button.setEnabled(playing)
        self.quiet_button.setEnabled(has or busy)
        self.playback_progress.setEnabled(caps['can_play'])
        self.folder_button.setEnabled(caps['current_export'])
        self.arm_button.setEnabled(caps['current_export'] and not c.player.alive)
        self.stop_button.setEnabled(c.player.alive or (busy and c.jobs.current.follow_up in (FollowUp.GAME, FollowUp.ARM)))
        self.roll.set_read_only(not caps['can_edit'] or self.compact)
        self.undo_button.setEnabled(caps['can_edit'] and self.roll.can_undo)
        self.redo_button.setEnabled(caps['can_edit'] and self.roll.can_redo)
        self.delete_button.setEnabled(caps['can_edit'] and not self.compact)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(1 if s.result else 0)
        self.setWindowTitle(('● ' if s.project_dirty else '') + '口琴工坊 · Harmonica Studio')

    def cancel_job(self):
        self.controller.jobs.cancel()
    def choose_file(self):
        path,_=QFileDialog.getOpenFileName(self,'打开 MIDI 曲谱','', 'MIDI 曲谱 (*.mid *.midi *.kar *.rmi)')
        if path:self.load_file(path)
    def choose_project(self):
        path,_=QFileDialog.getOpenFileName(self,'打开口琴工程','', '口琴工程 (*.hstudio)')
        if path:self.open_project(path)
    def load_example(self):self.load_file(default_library_root()/'欢乐颂.mid')
    def restore_last(self):self.open_project(self.controller.home/'autosave.hstudio')


    def load_file(self, path, sample_options=None, *, prepare=False, autoplay=False):
        if not self.controller.busy:
            self.presenter.invoke(self.controller.load_file, path, sample_options,
                                  prepare=prepare or self.compact, autoplay=autoplay)

    def install_parts(self):
        s = self.controller.state
        self.table.setRowCount(len(s.keys))
        selected = s.keys.index(s.selected_part) if s.selected_part in s.keys else 0
        def pitch(p):
            return ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'][p%12]+str(p//12-1)
        for row, key in enumerate(s.keys):
            notes = s.parts[key]
            duration = max(n['end'] for n in notes) - min(n['start'] for n in notes)
            values = [('★ ' if row == selected else '') + f'{s.names.get(key[0], "未命名")} · 轨 {key[0]} / 通道 {key[1]+1}',
                      str(len(notes)), pitch(min(n['pitch'] for n in notes)) + ' – ' + pitch(max(n['pitch'] for n in notes)), f'{duration:.1f} 秒']
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self.table.selectRow(selected)
        self.filehint.setText(f'{len(s.parts)} 个声部 · 已排除打击乐 · 原文件保持不变')

    def selected_options(self):
        row=self.table.currentRow()
        if row<0 or row>=len(self.controller.state.keys):raise ValueError('请先选择一个声部。')
        track,channel=self.controller.state.keys[row]
        if self.compact:return Options(track=track,channel=channel,skip_long_rests=self.controller.preferences.skip_long_rests)
        return Options(speed=self.speed.value(),transpose=self.transpose.value(),auto_octave=self.octave.isChecked(),trim_silence=self.trim.isChecked(),melody_mode=self.mode.currentData(),track=track,channel=channel,skip_long_rests=self.controller.preferences.skip_long_rests,phrase_octave=self.phrase_octave.isChecked())

    def begin_convert(self):
        if self.controller.busy or self.controller.state.source is None:
            return
        try:
            self.controller.begin_convert(self.selected_options(), persist=not self.compact)
        except Exception as exc:
            self.controller.report_error(exc)

    def open_project(self, path):
        if not self.controller.busy:
            self.presenter.invoke(self.controller.open_project, path)

    def save_current(self):
        if self.controller.state.project is None or self.controller.jobs.current:return
        path,_=QFileDialog.getSaveFileName(self,'保存口琴工程',str(self.controller.state.project_path or self.controller.home/'曲谱.hstudio'),'口琴工程 (*.hstudio)')
        if path:
            try:self.save_to(path)
            except Exception as exc:self.show_error(exc)

    def save_to(self, path):
        return self.controller.save_to(path)

    def autosave(self):
        try:
            self.controller.autosave()
        except Exception:
            logging.exception('Autosave failed')
            self.status.setText('自动暂存失败，请点击保存工程选择其他位置。')

    def notes_changed(self, notes):
        self.presenter.invoke(self.controller.replace_notes, notes)

    def update_summary(self):
        if self.controller.state.project is None:return
        removed=self.controller.state.project.get('report',{}).get('removed_rest_seconds',0) if not self.controller.state.export_dirty else 0
        rest_info=f'  /  已缩短 {removed:.1f} 秒空白' if isinstance(removed,(int,float)) and removed>0 else ''
        report=self.controller.state.project.get('report',{})
        adjusted=report.get('phrase_adjusted_notes',0);dropped=report.get('dropped_out_of_range',0)
        import_info=(f'  /  提取时按句调整 {adjusted} 音' if adjusted else '')+(f'  /  超音域丢弃 {dropped} 音' if dropped else '')
        self.summary.setText(f"{len(self.controller.state.project['notes'])} 音  /  {self.logical_duration():.1f} 秒"+rest_info+import_info+('  /  待生成' if self.controller.state.export_dirty else ''))
        changes=report.get('octave_adjustments',[])
        if not isinstance(changes,list):changes=[]
        details=['提取时的八度调整（相对整体移调；编辑后仍保留原记录）：'] if changes else []
        details.extend(f"{a['start']:.2f}–{a['end']:.2f} 秒：{a['semitones']:+d} 半音，{a['notes']} 音" for a in changes[:20] if isinstance(a,dict) and all(type(a.get(k)) in (int,float) for k in ('start','end')) and all(type(a.get(k)) is int for k in ('semitones','notes')))
        if len(changes)>20:details.append('其余乐句见导出文件中的转换报告。')
        self.summary.setToolTip('\n'.join(details))

    def begin_export(self, checked=False, *, play_after=False):
        if not self.controller.busy:
            self.presenter.invoke(self.controller.begin_export,
                                  follow_up=FollowUp.PLAY if play_after else FollowUp.NONE)

    def show_result(self, folder, report, replace_project=False):
        self.controller.show_result(folder, report, replace_project)


    def poll(self):
        self.controller.poll()

    def logical_duration(self):
        return self.controller.state.score_duration
    def display_position(self,position,state,show_cursor=True):
        total=self.controller.state.preview_duration or self.logical_duration();position=max(0,min(total,position))
        def stamp(t):return f'{int(t)//60:02d}:{int(t)%60:02d}'
        self.playback_progress.setRange(0,max(0,round(total*1000)))
        self.playback_progress.set_playback_position(position)
        text=f'{stamp(position)} / {stamp(total)}'
        if self.playback_time.text()!=text:self.playback_time.setText(text)
        if self.playback_state.text()!=state:self.playback_state.setText(state)
        logical=self.controller.to_score(position) if self.controller.state.preview_duration else position
        self.roll.set_position(logical if show_cursor else None,self.logical_duration())

    def seek_editor(self, seconds):
        self.presenter.invoke(self.controller.seek_score, seconds)
    def seek_audio(self, seconds):
        self.presenter.invoke(self.controller.seek_audio, seconds)

    def listen(self):
        if self.controller.busy:
            return
        if self.compact and self.controller.state.project is None and self.controller.state.parts:
            self.begin_convert()
        else:
            self.presenter.invoke(self.controller.listen)
    def pause_listening(self):
        self.presenter.invoke(self.controller.pause)
    def stop_listening(self):
        self.presenter.invoke(self.controller.stop_preview)
    def update_playback(self):
        self.presenter.invoke(self.controller.update_playback)

    def animate_playback(self):
        if self.controller.state.transport=='playing' and not self.playback_progress.isSliderDown():
            self.display_position(self.controller.clock.position(duration=self.controller.state.preview_duration),'试听中')

    def open_export(self):
        if self.controller.state.result and not self.controller.state.export_dirty:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.controller.state.result[0])))
    def arm(self):
        self.presenter.invoke(self.controller.game_play, arm=True)
    def stop_script(self):
        self.presenter.invoke(self.controller.stop_game, close=True)
    def show_error(self, exc):
        self.status.setText('未完成：' + str(exc))
        QMessageBox.warning(self, '需要处理', str(exc))
    def dragEnterEvent(self,event):
        if not self.controller.jobs.current and event.mimeData().hasUrls() and any(u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in ('.mid','.midi','.rmi','.kar','.hstudio') for u in event.mimeData().urls()):event.acceptProposedAction()
    def dropEvent(self,event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path=Path(url.toLocalFile())
                if path.suffix.lower()=='.hstudio':self.open_project(path);break
                if path.suffix.lower() in ('.mid','.midi','.rmi','.kar'):self.load_file(path);break
    def render_document(self):
        self.autosave_timer.stop()
        state = self.controller.state
        self.roll.set_notes(state.project['notes'] if state.project else [])
        if not state.parts:
            self.table.setRowCount(0)
        self.filename.setText(state.project['title'] if state.project else state.source.stem if state.source else '选择一首曲谱')
        self.tabs.setCurrentIndex(1 if state.project is not None or self.compact else 0)

    def open_remote(self):
        if self.remote_control.server and self.remote_control.server.active and self.remote_dialog:
            self.remote_dialog.show();self.remote_dialog.raise_();self.remote_dialog.activateWindow()
            return
        try:
            addresses = lan_addresses()
            server = self.start_remote(allowed_hosts=[value for _, value in addresses])
            self.remote_dialog = RemoteDialog(server, addresses, self)
            self.remote_dialog.show()
        except Exception as exc:
            self.show_error(exc)

    def start_remote(self, **options):
        server = self.remote_control.start(**options)
        self.remote_timer.start(50)
        self.remote_button.setText('手机遥控 · 已开启')
        return server

    def stop_remote(self):
        self.remote_timer.stop()
        self.remote_control.stop()
        self.remote_button.setText('手机遥控')
        if self.remote_dialog:
            self.remote_dialog.timer.stop();self.remote_dialog.hide();self.remote_dialog.deleteLater()
            self.remote_dialog = None

    def poll_remote(self):
        self.remote_control.poll()

    def remote_snapshot(self):
        return self.remote_control.snapshot()

    def remote_score_snapshot(self):
        return self.remote_control.score_snapshot()

    def handle_remote_command(self, command):
        return self.remote_control.handle(command)

    def closeEvent(self, event):
        try:
            self.controller.close()
        except Exception as exc:
            self.show_error(exc)
            event.ignore()
            return
        self.stop_remote()
        self.presenter.close()
        for timer in (self.autosave_timer, self.timer, self.animation_timer, self.library_timer):
            timer.stop()
        self.library_watcher.blockSignals(True)
        if self.library_watcher.directories():
            self.library_watcher.removePaths(self.library_watcher.directories())
        event.accept()

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
