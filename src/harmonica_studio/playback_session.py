"""Playback resources and transport, called only by the owning controller thread.

The session updates playback fields of the shared AppState and publishes display
events. It never changes documents, saves files, submits jobs or decides their
follow-up actions. Desktop and remote commands still enter through AppController.
"""
import logging

from .app_state import Transport
from .playback import AudioPlayer, ScriptPlayer
from .transport import PlaybackClock, TimeMap


class PlaybackSession:
    def __init__(self, state, emit, control_directory, *, audio=None, player=None, clock=None):
        self.state = state
        self.emit = emit
        self.audio = audio if audio is not None else AudioPlayer()
        self.player = player if player is not None else ScriptPlayer(control_directory)
        self.clock = clock if clock is not None else PlaybackClock()
        self.manual_seek = False
        self.set_time_anchors([])

    def set_time_anchors(self, anchors):
        self.time_anchors = list(anchors)
        self.to_audio = TimeMap(self.time_anchors)
        self.to_score = TimeMap((b, a) for a, b in self.time_anchors)

    def install_maps(self, anchors, to_audio, to_score):
        # Prepared by the export worker; take ownership without rebuilding maps.
        self.time_anchors, self.to_audio, self.to_score = anchors, to_audio, to_score

    def position(self, value, label, show_cursor=True):
        s = self.state
        s.position = max(0, min(value, s.preview_duration or s.score_duration))
        s.position_label, s.show_cursor = label, show_cursor
        self.emit('position')

    def stop_preview(self):
        self.manual_seek = False
        self.audio.stop()
        self.state.transport = Transport.READY
        self.state.logical_seek = 0
        self.clock.reset()
        self.position(0, '已停止' if self.state.project is not None else '未播放', False)
        self.emit('reset_timeline')
        self.emit('changed')

    def reset_preview(self):
        self.state.preview_duration = 0
        self.set_time_anchors([])
        self.audio.close()
        self.stop_game(close=True)

    def invalidate_after_edit(self):
        self.manual_seek = False
        s = self.state
        s.preview_duration, s.logical_seek = 0, 0
        s.transport = Transport.READY
        self.set_time_anchors([])
        self.clock.reset()
        try:
            self.audio.close()
        except Exception:
            logging.exception('Could not close previous audio after edit')
        finally:
            self.stop_game(close=True)
            self.position(0, '待生成', False)

    def load_preview(self, folder):
        s = self.state
        s.preview_duration, s.transport = 0, Transport.READY
        self.audio.load(folder / '试听.wav')
        s.preview_duration = self.audio.duration
        self.seek_score(s.logical_seek)

    def seek_score(self, seconds):
        s = self.state
        s.logical_seek = max(0, min(s.score_duration, seconds))
        self.seek_audio(self.to_audio(s.logical_seek) if s.preview_duration else s.logical_seek)

    def seek_audio(self, seconds):
        s = self.state
        if s.preview_duration:
            resume = s.transport == Transport.PLAYING
            self.audio.seek(seconds, resume=resume)
            position = self.audio.position
            self.clock.reset(position, resume)
            s.logical_seek = self.to_score(position)
            if not resume and s.transport != Transport.PAUSED:
                s.transport = Transport.READY
            self.position(position, '试听中' if resume else '已暂停' if s.transport == Transport.PAUSED else '已定位')
        else:
            s.logical_seek = max(0, min(s.score_duration, seconds))
            self.position(s.logical_seek, '已定位')

    def listen(self, folder, *, start_score=None):
        s = self.state
        self.stop_game(close=True)
        if not s.preview_duration:
            self.load_preview(folder)
        if start_score is not None:
            self.seek_score(start_score)
        start = self.audio.position
        if start_score is None and (s.transport == Transport.ENDED or start >= s.preview_duration - .01):
            start, s.logical_seek = 0, 0
        self.audio.play(start)
        s.transport = Transport.PLAYING
        self.clock.reset(start, True)
        self.manual_seek = False
        self.position(start, '试听中')
        self.emit('changed')

    def pause(self):
        if self.state.transport != Transport.PLAYING:
            return
        self.audio.pause()
        self.state.transport = Transport.PAUSED
        position = self.audio.position
        self.clock.reset(position)
        self.state.logical_seek = self.to_score(position)
        self.position(position, '已暂停')
        self.emit('changed')

    def update_playback(self):
        s = self.state
        if s.transport != Transport.PLAYING:
            return
        position, playing = self.audio.position, self.audio.playing
        s.logical_seek = self.to_score(position)
        self.clock.synchronize(position, playing)
        if not playing:
            s.transport = Transport.ENDED
            self.manual_seek = False
        self.position(self.clock.position(duration=s.preview_duration) if playing else s.preview_duration,
                      '试听中' if playing else '已结束')

    def start_game(self, folder, *, arm=False, start_score=None):
        script = folder / '演奏脚本.ahk'
        options = {}
        if start_score is not None:
            # Millisecond rounding must not move a valid near-end marker past the last release.
            end = self.to_audio(self.state.score_duration)
            options['start_seconds'] = max(0, min(self.to_audio(start_score), end - .001))
        self.player.start(script, **options) if arm else self.player.play(script, **options)

    def stop_game(self, *, close=False):
        self.player.stop() if close else self.player.stop_playback()

    def reap(self):
        self.player.reap()

    def close(self):
        try:
            self.audio.close()
        except Exception:
            logging.exception('Could not close audio during shutdown')
        finally:
            self.stop_game(close=True)
