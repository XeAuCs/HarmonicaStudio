"""Shared desktop/remote application operations. No Qt or HTTP dependency."""
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
import logging
from pathlib import Path
import time
import uuid

from .app_state import AppState, FollowUp, Transport
from .jobs import JobKind, JobRunner
from .library_coordinator import LibraryCoordinator
from .models import Options
from .metrics import measured
from .paths import data_root, library_path, library_setting
from .playback_session import PlaybackSession
from .preferences import load_preferences, save_preferences
from .project import load_project, validate_project
from .save_coordinator import SaveCoordinator, SaveKind, SaveSnapshot
from .service import convert_prepared, export_project_prepared, load_export_result, load_ranked_midi
from .storage import save_options


@dataclass
class PendingAction:
    request: object
    action: object
    follow_up: FollowUp


class AppController:
    def __init__(self, home=None, *, audio=None, player=None, executor=None, clock=None,
                 metrics=None, before_work=None, library_executor=None, save_executor=None):
        self.home = Path(home or data_root())
        self.home.mkdir(parents=True, exist_ok=True)
        self.state = AppState()
        self.metrics = metrics
        self.preferences = load_preferences(self.home / 'preferences.json')
        self.playback = PlaybackSession(self.state, self.emit, self.home / 'control',
                                        audio=audio, player=player, clock=clock)
        self.jobs = JobRunner(executor, metrics=metrics, before_work=before_work)
        self.library_scanner = LibraryCoordinator(library_executor, metrics=metrics)
        self.saves = SaveCoordinator(save_executor, metrics=metrics)
        self._pending_action = None
        self.library = []
        self._listeners = []

    # Preserve existing backend injection and display access without duplicate ownership.
    @property
    def audio(self):
        return self.playback.audio

    @audio.setter
    def audio(self, value):
        self.playback.audio = value

    @property
    def player(self):
        return self.playback.player

    @player.setter
    def player(self, value):
        self.playback.player = value

    @property
    def clock(self):
        return self.playback.clock

    @clock.setter
    def clock(self, value):
        self.playback.clock = value

    @property
    def time_anchors(self):
        return self.playback.time_anchors

    @property
    def to_audio(self):
        return self.playback.to_audio

    @property
    def to_score(self):
        return self.playback.to_score

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
        available = not self.busy and not s.closed and s.transition is None
        return dict(can_open=available, can_save=available and s.project is not None,
                    can_export=available and s.has_notes,
                    can_play=available and s.has_notes,
                    can_edit=available and s.project is not None and s.transport != Transport.PLAYING,
                    can_convert=available and bool(s.parts),
                    current_export=available and s.result is not None and not s.export_dirty)

    def _idle(self):
        if self.state.closed:
            raise RuntimeError('应用已关闭。')
        if self.state.transition is not None:
            raise RuntimeError('正在保存当前工程，请稍候。')
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

    @measured
    def refresh_library(self, *, remote=False):
        if self.state.closed:
            raise RuntimeError('应用已关闭。')
        request = self.library_scanner.request(self.library_root(), remote=remote)
        self.state.library_error = ''
        self.emit('library_requested')
        return request.generation

    @property
    def library_refreshing(self):
        return self.library_scanner.busy

    @measured
    def install_library(self, entries, request):
        self.library = entries
        self.state.library_revision = request.generation
        self.state.library_root = request.root
        self.state.library_error = ''
        self.emit('library')

    def _poll_library(self):
        completed = self.library_scanner.take_completed()
        if completed is None:
            return
        request, job = completed
        outcome = 'ok'
        try:
            if request != self.library_scanner.latest or request.root != self.library_root():
                outcome = 'stale'
            elif job.cancel.is_set():
                outcome = 'cancelled'
            else:
                self.install_library(job.future.result(), request)
        except InterruptedError:
            outcome = 'cancelled'
        except Exception as exc:
            outcome = 'error'
            self.state.library_error = str(exc)
            self.report_error(exc, remote=request.remote)
        finally:
            if self.metrics is not None:
                self.metrics.record('job.result', 0, outcome=outcome,
                                    job=job.number, kind=job.kind.value, revision=request.generation)
            if not self.state.closed:
                try:
                    self.library_scanner.start_pending()
                except Exception as exc:
                    self.state.library_error = str(exc)
                    self.report_error(exc, remote=self.library_scanner.latest.remote)

    def set_time_anchors(self, anchors):
        self.playback.set_time_anchors(anchors)

    def position(self, value, label, show_cursor=True):
        self.playback.position(value, label, show_cursor)

    def stop_preview(self):
        self.jobs.clear_follow_up(FollowUp.PLAY)
        if self._pending_action is not None and self._pending_action.follow_up == FollowUp.PLAY:
            self._pending_action.follow_up = FollowUp.NONE
        self.playback.stop_preview()

    def reset_result(self):
        self.stop_preview()
        # Invalidate first even if the device cannot close; old artifacts must not be reused.
        self.state.result = None
        self.playback.reset_preview()

    @measured
    def preserve_current(self, *, remote=False):
        s = self.state
        if s.project_dirty:
            name = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.hstudio'
            return self.saves.submit(self._save_snapshot(),
                                     (self.home / 'recovery' / name, self.home / 'autosave.hstudio'),
                                     SaveKind.PRESERVE, remote=remote)
        if self.saving:
            return self.saves.submit(SaveSnapshot(s.document_id, s.revision, None), (),
                                     SaveKind.PRESERVE, remote=remote)

    @property
    def saving(self):
        return self.saves.busy

    def _save_snapshot(self):
        s = self.state
        return SaveSnapshot(s.document_id, s.revision, deepcopy(s.project))

    def _defer_until_saved(self, request, action, transition, follow_up=FollowUp.NONE):
        if request is None:
            action(follow_up)
            return True
        self._pending_action = PendingAction(request, action, follow_up)
        self.state.transition = transition
        self.status('正在保存当前工程…')
        self.emit('changed')
        return False

    def _poll_saves(self):
        completed = self.saves.take_completed()
        if completed is None:
            return None
        request, error = completed.request, completed.error
        s = self.state
        try:
            if error is not None:
                # A failed earlier manual save also aborts a queued switch/close.
                self._pending_action, s.transition = None, None
                s.save_error = str(error)
                if request.kind == SaveKind.AUTO:
                    logging.error('Autosave failed: %s', error)
                    self.status('自动暂存失败，请点击保存工程选择其他位置。')
                else:
                    self.report_error(error, remote=request.remote)
            elif request.snapshot.document == s.document_id:
                s.save_error = ''
                if request.kind == SaveKind.MANUAL:
                    s.project_path = request.paths[0]
                    s.saved_revision = request.snapshot.revision
                    self.status('工程已保存：' + str(s.project_path))
                if self.home / 'autosave.hstudio' in request.paths:
                    s.autosave_revision = request.snapshot.revision
            if error is None and self._pending_action is not None:
                pending = self._pending_action
                if pending.request.number == request.number:
                    self._pending_action, s.transition = None, None
                    if (request.snapshot.document, request.snapshot.revision) != (s.document_id, s.revision):
                        error = RuntimeError('工程已变化，请重新执行操作。')
                        s.save_error = str(error)
                        self.report_error(error, remote=request.remote)
                    else:
                        pending.action(pending.follow_up)
        except Exception as exc:
            error = exc
            self._pending_action, s.transition = None, None
            self.report_error(exc, remote=request.remote)
        finally:
            if self.metrics is not None:
                self.metrics.record('job.result', 0, outcome='error' if error else 'ok', kind='save',
                                    job=completed.job.number if completed.job is not None else None,
                                    revision=request.snapshot.revision, document=request.snapshot.document,
                                    save_kind=request.kind.value)
            self.saves.start_next()
            self.emit('changed')
        return error

    def wait_for_saves(self, timeout=30):
        """Drain on the owning thread for headless clients/teardown, never the Qt UI."""
        deadline, failure = time.monotonic() + timeout, None
        while self.saving:
            job = self.saves.jobs.current
            try:
                if job is not None:
                    job.future.exception(timeout=max(0, deadline - time.monotonic()))
                elif time.monotonic() >= deadline:
                    raise TimeoutError('等待保存超时。')
            except TimeoutError:
                self._pending_action, self.state.transition = None, None
                raise
            error = self._poll_saves()
            failure = failure or error
        if failure is not None:
            raise failure

    @measured
    def autosave(self):
        if self.state.closed or self.state.transition is not None:
            return
        request = None
        if self.state.project is not None:
            request = self.saves.submit(self._save_snapshot(), (self.home / 'autosave.hstudio',), SaveKind.AUTO)
        self.emit('changed')
        return request

    @measured
    def open_project(self, path):
        self._idle()
        project = load_project(path)
        project.setdefault('options', {})['skip_long_rests'] = self.preferences.skip_long_rests
        path = Path(path).resolve()
        return self._defer_until_saved(self.preserve_current(),
                                      lambda _: self._open_project(project, path), 'open')

    def _open_project(self, project, path):
        self.reset_result()
        s = self.state
        s.set_project(project, saved=True, new_document=True)
        s.project_path = path
        s.source, s.parts, s.names, s.keys = None, {}, {}, []
        self.emit('document')
        self.position(0, '未播放', False)
        self.status('工程已打开。音符可继续编辑；点试听会生成最新结果。')
        self.emit('changed')

    @measured
    def load_file(self, path, sample_options=None, *, prepare=False, autoplay=False, remote=False):
        self._idle()
        path = Path(path).resolve()
        sample_options = deepcopy(sample_options)
        return self._defer_until_saved(self.preserve_current(remote=remote),
                                      lambda next_action: self._load_file(path, sample_options, prepare,
                                                                         next_action == FollowUp.PLAY, remote),
                                      'load', FollowUp.PLAY if autoplay else FollowUp.NONE)

    def _load_file(self, path, sample_options, prepare, autoplay, remote):
        self.reset_result()
        s = self.state
        s.set_project(None, saved=True, new_document=True)
        s.project_path, s.parts, s.names, s.keys = None, {}, {}, []
        s.source = path
        s.selected_part = None
        if isinstance(sample_options, dict):
            track, channel = sample_options.get('track'), sample_options.get('channel')
            if type(track) is int and type(channel) is int:
                s.selected_part = (track, channel)
        self.jobs.start(JobKind.LOAD, s.revision, load_ranked_midi, s.source,
                        prepare=prepare, follow_up=FollowUp.PLAY if autoplay else FollowUp.NONE, remote=remote)
        self.emit('document')
        self.status('正在读取曲谱…')
        self.emit('changed')

    @measured
    def install_parts(self, result):
        s = self.state
        s.parts, s.names, s.keys = result.parts, result.names, result.keys
        if s.selected_part not in s.keys:
            s.selected_part = s.keys[0] if s.keys else None
        self.emit('parts')
        self.status('已推荐主旋律。生成后可直接在图上修谱。')

    def default_options(self):
        key = self.state.selected_part
        if key is None:
            raise ValueError('请先选择一个声部。')
        return Options(track=key[0], channel=key[1], skip_long_rests=self.preferences.skip_long_rests)

    @measured
    def begin_convert(self, options=None, *, follow_up=FollowUp.NONE, remote=False, persist=False):
        self._idle()
        if self.state.source is None:
            return
        options = options or self.default_options()
        options.validate()
        if persist:
            save_options(self.home / 'settings.json', options)
        return self._defer_until_saved(self.preserve_current(remote=remote),
                                      lambda next_action: self._begin_convert(options, next_action, remote),
                                      'convert', follow_up)

    def _begin_convert(self, options, follow_up, remote):
        self.reset_result()
        self.jobs.start(JobKind.CONVERT, self.state.revision, convert_prepared,
                        self.state.source, self.home / 'exports', options,
                        follow_up=follow_up, remote=remote)
        self.status('正在提取旋律并制作试听…')
        self.emit('changed')

    @measured
    def save_to(self, path):
        self._idle()
        path = Path(path)
        if path.suffix.lower() != '.hstudio':
            path = path.with_suffix('.hstudio')
        if self.state.project is None:
            return
        snapshot = self._save_snapshot()
        request = self.saves.submit(snapshot, (path.resolve(),), SaveKind.MANUAL)
        self.saves.submit(snapshot, (self.home / 'autosave.hstudio',), SaveKind.AUTO)
        self.status('正在保存工程…')
        self.emit('changed')
        return request

    @measured
    def replace_notes(self, notes):
        self._idle()
        s = self.state
        if s.project is None:
            return
        project = validate_project(dict(s.project, notes=notes))
        s.set_project(project)
        self.playback.invalidate_after_edit()
        self.status('修改已记录。点试听即可听到修改后的旋律。')
        self.emit('autosave')
        self.emit('changed')

    def update_preferences(self, preferences):
        if self.state.closed or self.state.transition is not None:
            raise RuntimeError('正在保存或关闭工程，请稍候。')
        preferences = replace(preferences, library_folder=library_setting(preferences.library_folder))
        if self.busy and preferences.skip_long_rests != self.preferences.skip_long_rests:
            raise RuntimeError('曲谱正在准备，请稍候。')
        save_preferences(self.home / 'preferences.json', preferences)
        timing_changed = preferences.skip_long_rests != self.preferences.skip_long_rests
        library_changed = preferences.library_folder != self.preferences.library_folder
        self.preferences = preferences
        if library_changed:
            self.refresh_library()
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

    @measured
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
        self.playback.stop_game(close=True)
        self.jobs.start(JobKind.EXPORT, self.state.revision, export_project_prepared, snapshot,
                        self.home / 'exports', follow_up=follow_up, remote=remote)
        self.status('正在为修改后的旋律生成试听、MIDI 和脚本…')
        self.emit('changed')

    @measured
    def show_result(self, folder, report, replace_project=False, *, prepared=None):
        result = prepared if prepared is not None else load_export_result(folder, report)
        project = result.project
        s = self.state
        if replace_project:
            s.set_project(project, new_document=True)
            s.project_path, s.logical_seek = None, 0
            self.emit('document')
        else:
            s.project = project
        s.result = (result.folder, result.report)
        s.export_revision = s.revision
        self.playback.install_maps(result.anchors, result.to_audio, result.to_score)
        self.emit('result')
        self.autosave()
        self.load_preview()
        self.status('曲谱已准备好，可以试听或演奏。')

    def load_preview(self):
        self.playback.load_preview(self.state.result[0])

    @measured
    def poll(self):
        if self.state.closed:
            return
        self._poll_saves()
        if self.state.closed:
            return
        self._poll_library()
        if self.state.closed:
            return
        try:
            self.update_playback()
            self.playback.reap()
        except Exception as exc:
            self.state.transport = Transport.READY
            self.report_error(exc)
        job = self.jobs.take_completed() if self.state.transition is None else None
        if job:
            outcome = 'ok'
            try:
                result = job.future.result()
                if job.cancel.is_set():
                    raise InterruptedError('转换已取消。')
                if job.revision != self.state.revision:
                    outcome = 'stale'
                    self.status('工程已变化，已忽略旧任务结果。')
                elif job.kind == JobKind.LOAD:
                    self.install_parts(result)
                    if job.prepare:
                        self.begin_convert(follow_up=job.follow_up, remote=job.remote)
                else:
                    self.show_result(result.folder, result.report,
                                     replace_project=job.kind == JobKind.CONVERT, prepared=result)
                    if job.follow_up == FollowUp.PLAY:
                        self.listen(remote=job.remote)
                    elif job.follow_up in (FollowUp.GAME, FollowUp.ARM):
                        self.game_play(remote=job.remote, arm=job.follow_up == FollowUp.ARM)
            except InterruptedError:
                outcome = 'cancelled'
                self.status('已取消；可以继续编辑或重新导出。')
            except Exception as exc:
                outcome = 'error'
                self.report_error(exc, remote=job.remote)
            finally:
                if self.metrics is not None:
                    self.metrics.record('job.result', 0, outcome=outcome,
                                        job=job.number, kind=job.kind.value, revision=job.revision)
        self.emit('changed')

    def seek_score(self, seconds):
        self.playback.seek_score(seconds)

    def seek_audio(self, seconds):
        self.playback.seek_audio(seconds)

    def listen(self, *, remote=False):
        self._idle()
        s = self.state
        if s.project is None:
            return
        if s.export_dirty or not s.result:
            self.begin_export(follow_up=FollowUp.PLAY, remote=remote)
            return
        self.playback.listen(s.result[0])

    def pause(self):
        self.playback.pause()

    def update_playback(self):
        self.playback.update_playback()

    def game_play(self, *, remote=False, arm=False):
        self._idle()
        if not self.state.has_notes:
            raise ValueError('请先选择歌曲。')
        if self.state.export_dirty or not self.state.result:
            self.begin_export(follow_up=FollowUp.ARM if arm else FollowUp.GAME, remote=remote)
            return
        self.stop_preview()
        self.playback.start_game(self.state.result[0], arm=arm)
        self.status('演奏器已就绪；切到口琴界面按 F6，3 秒后开始。F8 退出。' if arm else '已发送演奏指令，请保持游戏口琴界面在前台。')

    def stop_game(self, *, close=False):
        self.jobs.clear_follow_up(FollowUp.GAME)
        self.jobs.clear_follow_up(FollowUp.ARM)
        if self._pending_action is not None and self._pending_action.follow_up in (FollowUp.GAME, FollowUp.ARM):
            self._pending_action.follow_up = FollowUp.NONE
        self.playback.stop_game(close=close)
        self.status('已请求停止；演奏器会松开按键后退出。' if close else '游戏演奏已停止。')

    def close(self, *, wait=True):
        if self.state.closed:
            return True
        if self.state.transition != 'close':
            if self.state.project is None and not self.saving:
                self._pending_action, self.state.transition = None, None
                self._finish_close()
                return True
            snapshot = self._save_snapshot()
            paths = (self.home / 'autosave.hstudio',) if snapshot.project is not None else ()
            request = self.saves.submit(snapshot, paths, SaveKind.CLOSE)
            self._defer_until_saved(request, lambda _: self._finish_close(), 'close')
        if wait:
            self.wait_for_saves()
        return self.state.closed

    def _finish_close(self):
        self.saves.close()
        self.jobs.close()
        self.library_scanner.close()
        try:
            self.playback.close()
        finally:
            self.emit('close_ready')
            self.state.closed = True
            self._listeners.clear()
