"""HTTP adapter depending on application operations, never desktop widgets."""
from concurrent.futures import Future, TimeoutError
import hashlib
import json
import queue
from .app_state import Transport
from .remote import RemoteServer
from .theme import theme_palette
from .rests import compress_long_rests
from .schedule import build_events
from .preview import decode_events


def song_id(path):
    return hashlib.sha256(str(path.resolve()).casefold().encode('utf-8')).hexdigest()[:24]


class RemoteControl:
    def __init__(self, controller, *, blocked=lambda: False):
        self.controller, self.blocked = controller, blocked
        self.server = None
        self.queue = queue.Queue(maxsize=16)
        self._score_key = self._score = self._published_score = None

    def start(self, *, host='0.0.0.0', port=47638, allowed_hosts=None):
        if self.server and self.server.active:
            return self.server
        self.server = RemoteServer(self.queue_command)
        try:
            self.server.start(host=host, port=port, allowed_hosts=allowed_hosts)
        except OSError:
            if not port:
                raise
            self.server.start(host=host, port=0, allowed_hosts=allowed_hosts)
        self.server.publish(self.snapshot())
        return self.server

    def stop(self):
        while True:
            try:
                _, pending = self.queue.get_nowait()
            except queue.Empty:
                break
            if not pending.done():
                pending.set_result({'ok': False, 'message': '电脑已关闭遥控，请重新扫码。'})
        if self.server:
            self.server.stop()

    def queue_command(self, command):
        pending = Future()
        try:
            self.queue.put_nowait((command, pending))
        except queue.Full:
            return {'ok': False, 'message': '指令较多，请稍后再试。'}
        try:
            return pending.result(timeout=3)
        except TimeoutError:
            pending.cancel()
            return {'ok': False, 'message': '电脑暂时没有响应，请稍后再试。'}

    def poll(self):
        if not self.server or not self.server.active:
            return
        for _ in range(4):
            try:
                command, pending = self.queue.get_nowait()
            except queue.Empty:
                break
            if pending.set_running_or_notify_cancel():
                pending.set_result(self.handle(command))
        self.server.publish(self.snapshot())

    def snapshot(self):
        c, s = self.controller, self.controller.state
        library = [{'id': song_id(e['path']), 'title': e['title'],
                    **({'duration_seconds': e['duration_seconds']} if e.get('duration_seconds') is not None else {})}
                   for e in c.library]
        duration = s.preview_duration or s.score_duration
        position = c.clock.position(duration=duration) if s.transport == Transport.PLAYING else s.position
        game = dict(c.player.status)
        game['available'] = c.capabilities()['can_play']
        score = self.score_snapshot()
        if self.server and self._published_score != (self.server, score['id']):
            self.server.publish_score(score)
            self._published_score = (self.server, score['id'])
        return dict(title=s.project['title'] if s.project else s.source.stem if s.source else '选择一首曲谱',
                    song_id=song_id(s.source) if s.source else None, transport=s.transport.value,
                    position=position, duration=duration, busy=c.busy, status=s.message,
                    theme=c.preferences.theme, palette=theme_palette(c.preferences.theme), library=library,
                    can_play=c.capabilities()['can_play'], game=game, score_id=score['id'],
                    library_refreshing=c.library_refreshing, library_revision=s.library_revision,
                    library_error='曲库刷新失败，请检查电脑端状态。' if s.library_error else '',
                    saving=c.saving, transition=s.transition,
                    highlight=s.project.get('highlight') if s.project else None,
                    start_from_highlight=c.preferences.start_from_highlight)

    def score_snapshot(self):
        c, s = self.controller, self.controller.state
        notes = s.project['notes'] if s.project else []
        folder = s.result[0] if s.result and not s.export_dirty and notes else None
        key = (s.revision, folder, c.preferences.skip_long_rests)
        if self._score is not None and key == self._score_key:
            return self._score
        visible = notes
        if folder is not None:
            try:
                visible = json.loads((folder / '音符.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                performance, _, _ = compress_long_rests(visible, c.preferences.skip_long_rests)
                visible = decode_events(build_events(performance)[0]) if performance else []
        compact = [[round(n['start'], 6), round(n['end'], 6), n['pitch']] for n in visible]
        pitches = [n[2] for n in compact]
        low, high = (min(pitches) - 2, max(pitches) + 2) if pitches else (60, 72)
        if high - low < 12:
            center = (low + high) // 2
            low, high = center - 6, center + 6
        payload = dict(notes=compact, duration=max((n[1] for n in compact), default=0), low=low, high=high)
        revision = hashlib.sha256(json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()).hexdigest()[:24] if compact else 'empty'
        self._score, self._score_key = dict(id=revision, **payload), key
        return self._score

    def handle(self, command):
        c, action = self.controller, command['action']
        if c.state.closed:
            return {'ok': False, 'message': '电脑已关闭。'}
        if self.blocked() and action not in ('stop', 'game_stop'):
            return {'ok': False, 'message': '电脑上有对话框等待处理，请先关闭。'}
        if (c.busy or c.state.transition is not None) and action not in ('stop', 'game_stop', 'refresh'):
            return {'ok': False, 'message': '正在保存当前工程，请稍候。' if c.state.transition else '曲谱正在准备，请稍候。'}
        try:
            if action == 'select':
                entry = next((e for e in c.library if song_id(e['path']) == command['song_id']), None)
                if not entry or not entry['path'].is_file():
                    c.refresh_library(remote=True)
                    return {'ok': False, 'message': '这首歌已不在曲库中，请刷新。'}
                c.load_file(entry['path'], entry.get('options'), prepare=True,
                            autoplay=command.get('autoplay', False), remote=True)
            elif action == 'refresh':
                c.refresh_library(remote=True)
                return {'ok': True, 'message': '正在刷新曲库…'}
            elif action in ('play', 'game_play'):
                if not c.state.has_notes:
                    return {'ok': False, 'message': '请先从曲库选择歌曲。'}
                c.listen(remote=True) if action == 'play' else c.game_play(remote=True)
            elif action == 'pause':
                c.pause()
            elif action == 'seek':
                if c.state.project is None:
                    return {'ok': False, 'message': '请先选择歌曲。'}
                c.seek_audio(min(command['position'], c.state.preview_duration or c.state.score_duration))
            elif action == 'stop':
                c.stop_preview()
                c.status('试听已停止。')
            elif action == 'game_stop':
                c.stop_game()
            else:
                return {'ok': False, 'message': '不支持这个操作。'}
            return {'ok': True, 'message': c.state.message}
        except Exception as exc:
            c.report_error(exc, remote=True)
            return {'ok': False, 'message': '操作未完成，请检查电脑端状态。'}
