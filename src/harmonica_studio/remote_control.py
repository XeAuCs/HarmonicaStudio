"""Bridge authenticated browser commands onto the existing Qt main thread."""
from concurrent.futures import Future, TimeoutError
import hashlib
import json
import queue

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .remote import RemoteServer
from .remote_ui import RemoteDialog, lan_addresses
from .theme import theme_palette
from .rests import compress_long_rests
from .schedule import build_events
from .preview import decode_events


def song_id(path):
    return hashlib.sha256(str(path.resolve()).casefold().encode('utf-8')).hexdigest()[:24]


class RemoteControlMixin:
    def setup_remote(self):
        self.remote = None;self.remote_dialog = None
        self._remote_queue = queue.Queue(maxsize=16)
        self._remote_dispatching = False;self._remote_job = False;self._remote_error = False
        self._remote_message = '';self._remote_prepare = False;self._remote_autoplay = False
        self._remote_score_key = None;self._remote_score_notes = None;self._remote_score = None
        self._remote_published_score = None
        self.remote_timer = QTimer(self);self.remote_timer.timeout.connect(self.poll_remote)

    def open_remote(self):
        if self.remote and self.remote.active:
            self.remote_dialog.show();self.remote_dialog.raise_();self.remote_dialog.activateWindow();return
        try:
            addresses = lan_addresses()
            self.start_remote(allowed_hosts=[value for _, value in addresses])
            self.remote_dialog = RemoteDialog(self.remote, addresses, self)
            self.remote_dialog.show()
        except Exception as exc:self.show_error(exc)

    def start_remote(self, *, host='0.0.0.0', port=47638, allowed_hosts=None):
        if self.remote and self.remote.active:return self.remote
        self.remote = RemoteServer(self.queue_remote_command)
        try:self.remote.start(host=host, port=port, allowed_hosts=allowed_hosts)
        except OSError:
            if not port:raise
            self.remote.start(host=host, port=0, allowed_hosts=allowed_hosts)
        self.remote.publish(self.remote_snapshot());self.remote_timer.start(50)
        self.remote_button.setText('手机遥控 · 已开启')
        return self.remote

    def stop_remote(self):
        self.remote_timer.stop()
        while True:
            try:_, pending = self._remote_queue.get_nowait()
            except queue.Empty:break
            if not pending.done():pending.set_result({'ok':False, 'message':'电脑已关闭遥控，请重新扫码。'})
        if self.remote:self.remote.stop()
        self.remote_button.setText('手机遥控')
        if self.remote_dialog:
            self.remote_dialog.timer.stop();self.remote_dialog.hide();self.remote_dialog.deleteLater();self.remote_dialog = None

    def queue_remote_command(self, command):
        pending = Future()
        try:self._remote_queue.put_nowait((command, pending))
        except queue.Full:return {'ok':False, 'message':'指令较多，请稍后再试。'}
        try:return pending.result(timeout=3)
        except TimeoutError:
            pending.cancel()
            return {'ok':False, 'message':'电脑暂时没有响应，请稍后再试。'}

    def poll_remote(self):
        if not self.remote or not self.remote.active:return
        for _ in range(4):
            try:command, pending = self._remote_queue.get_nowait()
            except queue.Empty:break
            if not pending.set_running_or_notify_cancel():continue
            try:result = self.handle_remote_command(command)
            except Exception:result = {'ok':False, 'message':'操作未完成，请检查电脑端状态。'}
            pending.set_result(result)
        self.remote.publish(self.remote_snapshot())

    def remote_snapshot(self):
        entries = getattr(self, '_library_entries', [])
        library = [{'id':song_id(e['path']), 'title':e['title'],
                    **({'duration_seconds':e['duration_seconds']} if e.get('duration_seconds') is not None else {})} for e in entries]
        duration = self.preview_duration or self.logical_duration()
        position = self.visual_clock.position(duration=duration) if self.transport == 'playing' else self.playback_progress.value()/1000
        has_notes = bool(self.project and self.project['notes'])
        game = dict(self.player.status)
        game['available'] = has_notes and not self.future
        score = self.remote_score_snapshot()
        if self.remote and self._remote_published_score != (self.remote, score['id']):
            self.remote.publish_score(score);self._remote_published_score = (self.remote, score['id'])
        return {'title':self.project['title'] if self.project else self.source.stem if self.source else '选择一首曲谱',
                'song_id':song_id(self.source) if self.source else None,
                'transport':self.transport, 'position':position, 'duration':duration,
                'busy':self.future is not None,
                'status':('正在读取曲谱…' if self.job_kind == 'load' else '正在准备曲谱…') if self.future else self._remote_message,
                'theme':self.preferences.theme, 'palette':theme_palette(self.preferences.theme),
                'library':library, 'can_play':has_notes and not self.future, 'game':game, 'score_id':score['id']}

    def remote_score_snapshot(self):
        """Cache the exact scheduled score; transport ticks only send its revision.

        The exported note table is also used to render WAV and schedule AHK.
        Unsaved edits use score time until a fresh export replaces that table.
        Retaining the note list prevents Python id reuse from hitting this cache.
        """
        notes = self.project['notes'] if self.project else None
        folder = self.result[0] if self.result and not self.export_dirty and notes else None
        key = (id(notes), folder, self.preferences.skip_long_rests)
        if self._remote_score is not None and self._remote_score_key == key:
            return self._remote_score
        visible = notes or []
        if folder is not None:
            try:visible = json.loads((folder/'音符.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                performance, _, _ = compress_long_rests(visible, self.preferences.skip_long_rests)
                visible = decode_events(build_events(performance)[0]) if performance else []
        compact = [[round(n['start'], 6), round(n['end'], 6), n['pitch']] for n in visible]
        pitches = [n[2] for n in compact]
        low, high = (min(pitches)-2, max(pitches)+2) if pitches else (60, 72)
        if high-low < 12:
            center = (low+high)//2;low, high = center-6, center+6
        payload = {'notes':compact, 'duration':max((n[1] for n in compact), default=0), 'low':low, 'high':high}
        revision = hashlib.sha256(json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()).hexdigest()[:24] if compact else 'empty'
        self._remote_score = dict(id=revision, **payload)
        self._remote_score_key = key;self._remote_score_notes = notes
        return self._remote_score

    def handle_remote_command(self, command):
        action = command['action']
        if QApplication.activeModalWidget() is not None and action not in ('stop', 'game_stop'):
            return {'ok':False, 'message':'电脑上有对话框等待处理，请先关闭。'}
        if self.future and action not in ('stop', 'game_stop', 'refresh'):
            return {'ok':False, 'message':'曲谱正在准备，请稍候。'}
        self._remote_dispatching = True;self._remote_error = False
        try:
            if action == 'select':
                entry = next((e for e in self._library_entries if song_id(e['path']) == command['song_id']), None)
                if not entry or not entry['path'].is_file():
                    self.refresh_library();return {'ok':False, 'message':'这首歌已不在曲库中，请刷新。'}
                self.load_file(entry['path'], entry.get('options'), prepare=True, autoplay=command.get('autoplay', False))
                self._remote_message = '已选歌，正在准备…'
            elif action == 'refresh':
                self.refresh_library();self._remote_message = '曲库已刷新。'
            elif action in ('play', 'game_play'):
                if not self.project or not self.project['notes']:return {'ok':False, 'message':'请先从曲库选择歌曲。'}
                self._remote_job = True
                if action == 'play':self.listen();self._remote_message = '在电脑上试听。'
                elif self.export_dirty or not self.result:
                    self.begin_export();self.after_export = 'game';self._remote_message = '正在为游戏演奏准备曲谱…'
                else:self.remote_game_play()
            elif action == 'pause':
                if self.transport == 'playing':self.pause_listening()
                self._remote_message = '试听已暂停。'
            elif action == 'seek':
                if not self.project:return {'ok':False, 'message':'请先选择歌曲。'}
                self.seek_audio(min(command['position'], self.preview_duration or self.logical_duration()))
                self._remote_message = '试听位置已更新。'
            elif action == 'stop':
                self._remote_autoplay = False
                if self.after_export == 'play':self.after_export = None
                self.stop_listening();self._remote_message = '试听已停止。'
            elif action == 'game_stop':
                if self.after_export == 'game':self.after_export = None
                self.player.stop_playback();self._remote_message = '游戏演奏已停止。'
            else:return {'ok':False, 'message':'不支持这个操作。'}
            if self._remote_error:return {'ok':False, 'message':'操作未完成，请检查电脑端状态。'}
            return {'ok':True, 'message':self._remote_message}
        finally:
            self._remote_dispatching = False
            if not self.future:self._remote_job = False

    def remote_game_play(self):
        self.stop_listening()
        self.player.play(self.result[0]/'演奏脚本.ahk')
        self._remote_message = '已发送演奏指令，请保持游戏口琴界面在前台。'
