"""Shared desktop/remote application operations. No Qt or HTTP dependency."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
import logging
from pathlib import Path
import uuid

from .app_state import AppState, FollowUp, Transport
from .jobs import JobKind, JobRunner
from .library import sample_entries
from .melody import rank_parts
from .midi import read_midi
from .models import Options
from .paths import data_root, library_path, library_setting
from .playback import AudioPlayer, ScriptPlayer
from .preferences import load_preferences, save_preferences
from .project import load_project, save_project, validate_project
from .service import convert, export_project
from .storage import save_options
from .transport import PlaybackClock, TimeMap, playback_anchors


class AppController:
    def __init__(self, home=None, *, audio=None, player=None, executor=None, clock=None):
        self.home = Path(home or data_root())
        self.home.mkdir(parents=True, exist_ok=True)
        self.state = AppState()
        self.preferences = load_preferences(self.home / 'preferences.json')
        self.audio = audio if audio is not None else AudioPlayer()
        self.player = player if player is not None else ScriptPlayer(self.home / 'control')
        self.jobs = JobRunner(executor)
        self.clock = clock if clock is not None else PlaybackClock()
        self.time_anchors = []
        self.to_audio = TimeMap()
        self.to_score = TimeMap()
        self.library = []
        self._listeners = []

    def subscribe(self, listener):
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def emit(self, event, value=None):
        if not self.state.closed:
            for listener in tuple(self._listeners):
                listener(event, value)

    @property
    def busy(self):
        return self.jobs.current is not None

    def capabilities(self):
        s = self.state
        available = not self.busy and not s.closed
        return dict(can_save=available and s.project is not None,
                    can_export=available and s.has_notes,
                    can_play=available and s.has_notes,
                    can_edit=available and s.project is not None and s.transport != Transport.PLAYING,
                    can_convert=available and bool(s.parts),
                    current_export=available and s.result is not None and not s.export_dirty)

    def _idle(self):
        if self.state.closed:
            raise RuntimeError('应用已关闭。')
        if self.busy:
            raise RuntimeError('曲谱正在准备，请稍候。')

    def status(self, text):
        self.state.message = text
        self.emit('status', text)

    def report_error(self, exc, *, remote=False):
        logging.error('Operation failed: %s', exc, exc_info=True)
        self.status('未完成：' + str(exc))
        self.emit('error', (exc, remote))

    def library_root(self):
        return library_path(self.preferences.library_folder)

    def refresh_library(self):
        self.library = sample_entries(self.library_root())
        self.emit('library')

    def set_time_anchors(self, anchors):
        self.time_anchors = list(anchors)
        self.to_audio = TimeMap(self.time_anchors)
        self.to_score = TimeMap((b, a) for a, b in self.time_anchors)

    def position(self, value, label, show_cursor=True):
        s = self.state
        s.position = max(0, min(value, s.preview_duration or s.score_duration))
        s.position_label, s.show_cursor = label, show_cursor
        self.emit('position')

    def stop_preview(self):
        self.jobs.clear_follow_up(FollowUp.PLAY)
        self.audio.stop()
        self.state.transport = Transport.READY
        self.state.logical_seek = 0
        self.clock.reset()
        self.position(0, '已停止' if self.state.project is not None else '未播放', False)
        self.emit('reset_timeline')
        self.emit('changed')

    def reset_result(self):
        self.stop_preview()
        # Invalidate first even if the device cannot close; old artifacts must not be reused.
        self.state.result = None
        self.state.preview_duration = 0
        self.set_time_anchors([])
        self.audio.close()
        self.player.stop()

    def preserve_current(self):
        if self.state.project_dirty:
            name = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.hstudio'
            save_project(self.home / 'recovery' / name, self.state.project)
            self.autosave()

    def autosave(self):
        if self.state.project is not None:
            save_project(self.home / 'autosave.hstudio', self.state.project)
        self.emit('changed')

    def open_project(self, path):
        self._idle()
        project = load_project(path)
        project.setdefault('options', {})['skip_long_rests'] = self.preferences.skip_long_rests
        self.preserve_current()
        self.reset_result()
        s = self.state
        s.set_project(project, saved=True)
        s.project_path = Path(path).resolve()
        s.source, s.parts, s.names, s.keys = None, {}, {}, []
        self.emit('document')
        self.position(0, '未播放', False)
        self.status('工程已打开。音符可继续编辑；点试听会生成最新结果。')
        self.emit('changed')

    def load_file(self, path, sample_options=None, *, prepare=False, autoplay=False, remote=False):
        self._idle()
        self.preserve_current()
        self.reset_result()
        s = self.state
        s.set_project(None, saved=True)
        s.project_path, s.parts, s.names, s.keys = None, {}, {}, []
        s.source = Path(path).resolve()
        s.selected_part = None
        if isinstance(sample_options, dict):
            track, channel = sample_options.get('track'), sample_options.get('channel')
            if type(track) is int and type(channel) is int:
                s.selected_part = (track, channel)
        self.jobs.start(JobKind.LOAD, s.revision, read_midi, s.source, cancellable=False,
                        prepare=prepare, follow_up=FollowUp.PLAY if autoplay else FollowUp.NONE, remote=remote)
        self.emit('document')
        self.status('正在读取曲谱…')
        self.emit('changed')

    def install_parts(self, parts, names):
        s = self.state
        s.parts, s.names = parts, names
        s.keys = [key for key, _ in rank_parts(parts, names)]
        if s.selected_part not in s.keys:
            s.selected_part = s.keys[0] if s.keys else None
        self.emit('parts')
        self.status('已推荐主旋律。生成后可直接在图上修谱。')

    def default_options(self):
        key = self.state.selected_part
        if key is None:
            raise ValueError('请先选择一个声部。')
        return Options(track=key[0], channel=key[1], skip_long_rests=self.preferences.skip_long_rests)

    def begin_convert(self, options=None, *, follow_up=FollowUp.NONE, remote=False, persist=False):
        self._idle()
        if self.state.source is None:
            return
        options = options or self.default_options()
        options.validate()
        if persist:
            save_options(self.home / 'settings.json', options)
        self.preserve_current()
        self.reset_result()
        self.jobs.start(JobKind.CONVERT, self.state.revision, convert,
                        self.state.source, self.home / 'exports', options,
                        follow_up=follow_up, remote=remote)
        self.status('正在提取旋律并制作试听…')
        self.emit('changed')

    def save_to(self, path):
        self._idle()
        path = Path(path)
        if path.suffix.lower() != '.hstudio':
            path = path.with_suffix('.hstudio')
        save_project(path, self.state.project)
        self.state.project_path = path.resolve()
        self.state.saved_revision = self.state.revision
        self.autosave()
        self.status('工程已保存：' + str(path))

    def replace_notes(self, notes):
        self._idle()
        s = self.state
        if s.project is None:
            return
        project = validate_project(dict(s.project, notes=notes))
        s.set_project(project)
        s.preview_duration, s.logical_seek = 0, 0
        s.transport = Transport.READY
        self.set_time_anchors([])
        self.clock.reset()
        try:
            self.audio.close()
        except Exception:
            logging.exception('Could not close previous audio after edit')
        finally:
            self.player.stop()
            self.position(0, '待生成', False)
            self.status('修改已暂存。点试听即可听到修改后的旋律。')
            self.emit('autosave')
            self.emit('changed')

    def update_preferences(self, preferences):
        preferences = replace(preferences, library_folder=library_setting(preferences.library_folder))
        if self.busy and preferences.skip_long_rests != self.preferences.skip_long_rests:
            raise RuntimeError('曲谱正在准备，请稍候。')
        save_preferences(self.home / 'preferences.json', preferences)
        timing_changed = preferences.skip_long_rests != self.preferences.skip_long_rests
        self.preferences = preferences
        if timing_changed and self.state.project is not None:
            position = self.state.logical_seek
            self.reset_result()
            project = deepcopy(self.state.project)
            project.setdefault('options', {})['skip_long_rests'] = preferences.skip_long_rests
            self.state.set_project(project)
            self.state.logical_seek = position
            self.position(position, '待生成')
            self.status('播放设置已保存，下次试听或游戏演奏将使用新的停顿时长。')
            self.emit('autosave')
        self.emit('changed')

    def begin_export(self, *, follow_up=FollowUp.NONE, remote=False):
        self._idle()
        if self.state.project is None:
            return
        snapshot = validate_project(self.state.project)
        snapshot.setdefault('options', {})['skip_long_rests'] = self.preferences.skip_long_rests
        if not snapshot['notes']:
            raise ValueError('请先双击音符图添加音符。')
        target_position = self.state.logical_seek
        self.stop_preview()
        self.state.logical_seek = target_position
        self.player.stop()
        self.jobs.start(JobKind.EXPORT, self.state.revision, export_project, snapshot,
                        self.home / 'exports', follow_up=follow_up, remote=remote)
        self.status('正在为修改后的旋律生成试听、MIDI 和脚本…')
        self.emit('changed')

    def show_result(self, folder, report, replace_project=False):
        folder = Path(folder)
        project = load_project(folder / '工程.hstudio')
        actual = json.loads((folder / '音符.json').read_text(encoding='utf-8'))
        s = self.state
        if replace_project:
            s.set_project(project)
            s.project_path, s.logical_seek = None, 0
            self.emit('document')
        else:
            s.project = project
        s.result = (folder, report)
        s.export_revision = s.revision
        self.set_time_anchors(playback_anchors(project['notes'], actual))
        self.emit('result')
        self.autosave()
        self.load_preview()
        self.status('曲谱已准备好，可以试听或演奏。')

    def load_preview(self):
        s = self.state
        s.preview_duration, s.transport = 0, Transport.READY
        self.audio.load(s.result[0] / '试听.wav')
        s.preview_duration = self.audio.duration
        self.seek_score(s.logical_seek)

    def poll(self):
        if self.state.closed:
            return
        try:
            self.update_playback()
            self.player.reap()
        except Exception as exc:
            self.state.transport = Transport.READY
            self.report_error(exc)
        job = self.jobs.take_completed()
        if job:
            try:
                result = job.future.result()
                if job.cancel.is_set():
                    raise InterruptedError('转换已取消。')
                if job.revision != self.state.revision:
                    self.status('工程已变化，已忽略旧任务结果。')
                elif job.kind == JobKind.LOAD:
                    self.install_parts(*result)
                    if job.prepare:
                        self.begin_convert(follow_up=job.follow_up, remote=job.remote)
                else:
                    self.show_result(*result, replace_project=job.kind == JobKind.CONVERT)
                    if job.follow_up == FollowUp.PLAY:
                        self.listen(remote=job.remote)
                    elif job.follow_up in (FollowUp.GAME, FollowUp.ARM):
                        self.game_play(remote=job.remote, arm=job.follow_up == FollowUp.ARM)
            except InterruptedError:
                self.status('已取消；可以继续编辑或重新导出。')
            except Exception as exc:
                self.report_error(exc, remote=job.remote)
        self.emit('changed')

    def seek_score(self, seconds):
        self.state.logical_seek = max(0, min(self.state.score_duration, seconds))
        self.seek_audio(self.to_audio(self.state.logical_seek) if self.state.preview_duration else self.state.logical_seek)

    def seek_audio(self, seconds):
        s = self.state
        if s.preview_duration:
            resume = s.transport == Transport.PLAYING
            self.audio.seek(seconds, resume=resume)
            position = self.audio.position
            self.clock.reset(position, resume)
            s.logical_seek = self.to_score(position)
            if not resume:
                s.transport = Transport.READY
            self.position(position, '试听中' if resume else '已定位')
        else:
            s.logical_seek = max(0, min(s.score_duration, seconds))
            self.position(s.logical_seek, '已定位')

    def listen(self, *, remote=False):
        self._idle()
        s = self.state
        if s.project is None:
            return
        if s.export_dirty or not s.result:
            self.begin_export(follow_up=FollowUp.PLAY, remote=remote)
            return
        self.player.stop()
        if not s.preview_duration:
            self.load_preview()
        start = self.audio.position
        if s.transport == Transport.ENDED or start >= s.preview_duration - .01:
            start, s.logical_seek = 0, 0
        self.audio.play(start)
        s.transport = Transport.PLAYING
        self.clock.reset(start, True)
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
        self.position(self.clock.position(duration=s.preview_duration) if playing else s.preview_duration,
                      '试听中' if playing else '已结束')

    def game_play(self, *, remote=False, arm=False):
        self._idle()
        if not self.state.has_notes:
            raise ValueError('请先选择歌曲。')
        if self.state.export_dirty or not self.state.result:
            self.begin_export(follow_up=FollowUp.ARM if arm else FollowUp.GAME, remote=remote)
            return
        self.stop_preview()
        script = self.state.result[0] / '演奏脚本.ahk'
        self.player.start(script) if arm else self.player.play(script)
        self.status('演奏器已就绪；切到口琴界面按 F6，3 秒后开始。F8 退出。' if arm else '已发送演奏指令，请保持游戏口琴界面在前台。')

    def stop_game(self, *, close=False):
        self.jobs.clear_follow_up(FollowUp.GAME)
        self.jobs.clear_follow_up(FollowUp.ARM)
        self.player.stop() if close else self.player.stop_playback()
        self.status('已请求停止；演奏器会松开按键后退出。' if close else '游戏演奏已停止。')

    def close(self):
        if self.state.closed:
            return
        self.autosave()  # A failed save leaves the app open and usable.
        self.jobs.close()
        try:
            self.audio.close()
        except Exception:
            logging.exception('Could not close audio during shutdown')
        finally:
            try:
                self.player.stop()
            finally:
                self.state.closed = True
                self._listeners.clear()
