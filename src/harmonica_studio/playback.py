"""Explicit local audio playback and cooperative AHK lifecycle control."""
from pathlib import Path
import ctypes
import hashlib
import json
import math
import os
import subprocess, sys, uuid
import wave
from .paths import resource_root


def preview(path):
    if sys.platform != 'win32':
        raise RuntimeError('内置试听目前支持 Windows；也可用音乐播放器打开试听.wav。')
    import winsound
    winsound.PlaySound(str(path),winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)


def stop_preview():
    if sys.platform == 'win32':
        import winsound
        winsound.PlaySound(None,0)


class AudioPlayer:
    """One seekable local WAV player; all calls belong on the UI thread.

    ``sender`` is an optional MCI command transport for tests. Native initialization
    is deferred until the first command, so an unloaded player is portable.
    """

    def __init__(self, sender=None):
        self._sender = sender
        self._winmm = None
        self._alias = 'harmonica_' + uuid.uuid4().hex
        self._loaded = False
        self._duration_ms = 0
        self.path = None

    def _send(self, command):
        if self._sender is not None:
            return self._sender(command)
        if self._winmm is None:
            if sys.platform != 'win32':
                raise RuntimeError('内置试听目前支持 Windows；也可用音乐播放器打开试听.wav。')
            try:
                library = ctypes.WinDLL('winmm')
                library.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p,
                                                   ctypes.c_uint, ctypes.c_void_p]
                library.mciSendStringW.restype = ctypes.c_uint
                library.mciGetErrorStringW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p,
                                                       ctypes.c_uint]
                library.mciGetErrorStringW.restype = ctypes.c_int
                self._winmm = library
            except OSError as exc:
                raise RuntimeError('无法加载 Windows 音频播放器。') from exc
        answer = ctypes.create_unicode_buffer(256)
        code = self._winmm.mciSendStringW(command, answer, len(answer), None)
        if code:
            message = ctypes.create_unicode_buffer(512)
            found = self._winmm.mciGetErrorStringW(code, message, len(message))
            detail = message.value.strip() if found else '未知音频设备错误'
            raise RuntimeError(f'试听播放失败：{detail}（错误 {code}）。')
        return answer.value.strip()

    def _require_loaded(self):
        if not self._loaded:
            raise RuntimeError('请先生成或打开一份试听音频。')

    def _milliseconds(self, seconds):
        try:
            number = float(seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError('播放位置必须为有限数字。') from exc
        if not math.isfinite(number):
            raise ValueError('播放位置必须为有限数字。')
        number = min(self.duration, max(0, number))
        return min(self._duration_ms, round(number * 1000))

    @property
    def duration(self):
        """Loaded audio duration in seconds, with native millisecond precision."""
        return self._duration_ms / 1000

    @property
    def position(self):
        if not self._loaded:
            return 0.0
        value = self._send(f'status {self._alias} position')
        try:
            return min(self.duration, max(0.0, int(value) / 1000))
        except (TypeError, ValueError) as exc:
            raise RuntimeError('音频播放器返回了无法识别的播放位置。') from exc

    @property
    def playing(self):
        return self._loaded and self._send(f'status {self._alias} mode').lower() == 'playing'

    def load(self, path):
        """Close the previous sound and open a validated local PCM WAV file."""
        self.close()
        text = str(path)
        if any(c in text for c in ('"', '\0', '\r', '\n')):
            raise RuntimeError('试听音频路径包含无效字符。')
        target = Path(path).resolve()
        if not target.is_file():
            raise RuntimeError('找不到试听音频，请重新生成曲谱。')
        try:
            with wave.open(str(target), 'rb') as sound:
                if sound.getcomptype() != 'NONE' or not sound.getnframes():
                    raise RuntimeError('试听需要包含音频的 PCM WAV 文件。')
        except (OSError, EOFError, wave.Error) as exc:
            raise RuntimeError('无法读取试听音频，请使用有效的 PCM WAV 文件。') from exc
        self._send(f'open "{target}" type waveaudio alias {self._alias}')
        self._loaded = True
        try:
            self._send(f'set {self._alias} time format milliseconds')
            self._duration_ms = int(self._send(f'status {self._alias} length'))
            if self._duration_ms < 0:
                raise ValueError('negative length')
            self.path = target
        except (ValueError, TypeError) as exc:
            self.close()
            raise RuntimeError('音频播放器无法读取这份音频的时长。') from exc
        except Exception:
            self.close()
            raise

    def play(self, start_seconds=None):
        """Play asynchronously from the current or requested position.

        A position at the end stays stopped; explicitly play(0) to restart.
        """
        self._require_loaded()
        if start_seconds is not None:
            self.seek(start_seconds, resume=True)
        elif self.position < self.duration:
            self._send(f'play {self._alias}')

    def pause(self):
        if self.playing:
            self._send(f'pause {self._alias}')

    def stop(self):
        if self._loaded:
            self._send(f'seek {self._alias} to start wait')

    def seek(self, seconds, resume=False):
        self._require_loaded()
        position = self._milliseconds(seconds)
        target = 'end' if position == self._duration_ms else str(position)
        # MCI seek stops playback and aligns to the nearest complete audio sample.
        self._send(f'seek {self._alias} to {target} wait')
        if resume and position < self._duration_ms:
            self._send(f'play {self._alias}')

    def close(self):
        if self._loaded:
            self._send(f'close {self._alias}')
        self._loaded = False
        self._duration_ms = 0
        self.path = None


class ScriptPlayer:
    """Cooperative AHK process controlled only by this desktop application's UI.

    Every launch owns a fresh directory. Commands replace one small JSON file,
    so a stop written before AHK starts supersedes any earlier play request.
    AHK acknowledges the latest command id in its atomic status snapshots.
    """

    def __init__(self,control_dir):
        self.control_dir=Path(control_dir)
        self.process=None
        self.stop_file=None
        self.command_file=None
        self.status_file=None
        self._instance_dir=None
        self._script_signature=None
        self._command_id=0
        self._stopping=False
        self._pending_play=None
        self._last_status=self._snapshot('idle',message='演奏器未启动。')

    @staticmethod
    def _snapshot(state,position=0.0,duration=0.0,message=''):
        return dict(state=state,position=position,duration=duration,message=message)

    @staticmethod
    def _signature(script):
        path=Path(script).resolve()
        try:
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise RuntimeError('找不到演奏脚本，请先生成曲谱。') from exc
        return path,digest

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    @property
    def status(self):
        """A safe snapshot in event-table seconds; reads never generate input."""
        if not self.alive:
            if self._pending_play is not None:
                return self._snapshot('ready',message='正在切换演奏曲谱。')
            return self._snapshot('idle',duration=self._last_status['duration'],
                                  message=self._last_status['message'] if self._last_status['state']=='idle'
                                  else '演奏器已退出。')
        if self._stopping:
            return self._snapshot('idle',duration=self._last_status['duration'],message='正在停止并退出演奏器。')
        try:
            if self.status_file.stat().st_size > 16384:
                return dict(self._last_status)
            value=json.loads(self.status_file.read_text(encoding='utf-8-sig'))
            if not isinstance(value,dict) or value.get('request_id') != self._command_id:
                return dict(self._last_status)
            state=value.get('state')
            position,duration=float(value.get('position',0)),float(value.get('duration',0))
            if state not in ('idle','ready','countdown','playing') or not all(
                    math.isfinite(number) and number>=0 for number in (position,duration)):
                return dict(self._last_status)
            self._last_status=self._snapshot(state,min(position,duration),duration,str(value.get('message',''))[:500])
        except (OSError,ValueError,TypeError,AttributeError):
            # A missing/locked/partial status cannot interrupt desktop playback.
            pass
        return dict(self._last_status)

    def _cleanup(self):
        for path in (self.stop_file,self.command_file,self.status_file):
            if path is not None:
                try:path.unlink(missing_ok=True)
                except OSError:pass
        if self._instance_dir is not None:
            try:self._instance_dir.rmdir()
            except OSError:pass
        self.stop_file=self.command_file=self.status_file=None
        self._instance_dir=None
        self._script_signature=None

    def _command(self,action):
        if not self.alive or self.command_file is None or self._stopping:
            return
        request_id=self._command_id+1
        temporary=self.command_file.with_name('command.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_text(json.dumps(dict(id=request_id,action=action)),encoding='utf-8')
            os.replace(temporary,self.command_file)
        finally:
            temporary.unlink(missing_ok=True)
        self._command_id=request_id
        self._last_status=self._snapshot('ready',duration=self._last_status['duration'],
                                        message='正在请求开始演奏。' if action=='play' else '已请求停止演奏。')

    def start(self,script):
        if self.alive:
            raise RuntimeError('已有演奏器在运行，请先停止它。')
        signature=self._signature(script)
        exe=resource_root()/'third_party/AutoHotkey/AutoHotkey64.exe'
        if not exe.is_file():
            raise RuntimeError('缺少 AutoHotkey 运行文件，请查看工程 README 的运行环境说明。')
        self._cleanup()
        self._pending_play=None
        self._stopping=False
        self._command_id=0
        self.control_dir.mkdir(parents=True,exist_ok=True)
        self._instance_dir=self.control_dir/uuid.uuid4().hex
        self._instance_dir.mkdir()
        self.stop_file=self._instance_dir/'exit.stop'
        self.command_file=self._instance_dir/'command.json'
        self.status_file=self._instance_dir/'status.json'
        try:
            self.process=subprocess.Popen([str(exe),'/ErrorStdOut',str(signature[0]),str(self.stop_file),
                                           str(self.command_file),str(self.status_file)],
                                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                          creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        except OSError as exc:
            self.process=None
            self._cleanup()
            raise RuntimeError('无法启动演奏器，请检查 AutoHotkey 运行文件。') from exc
        self._script_signature=signature
        self._last_status=self._snapshot('ready',message='演奏器已就绪；切到口琴界面按 F6 或从手机开始。')

    def play(self,script):
        """Request play without toggling a running song into a stop.

        A changed score restarts the process cooperatively. The caller's normal
        reap() timer finishes that restart once the old process releases input.
        """
        signature=self._signature(script)
        if b'; Harmonica Studio remote protocol: 1' not in signature[0].read_bytes():
            raise RuntimeError('这份演奏脚本来自旧版本，请重新导出曲谱后再从手机开始。')
        if self.alive and (self._stopping or signature != self._script_signature):
            self.stop()
            self._pending_play=signature[0]
            return
        if not self.alive:
            self.start(signature[0])
        self._command('play')

    def stop_playback(self):
        """Stop and rewind input while leaving an armed process available."""
        self._pending_play=None
        self._command('stop')

    def stop(self):
        self._pending_play=None
        if self.alive and self.stop_file:
            # The AHK process notices this and releases held input before exiting.
            self.stop_file.touch()
            self._stopping=True
        self._last_status=self._snapshot('idle',duration=self._last_status['duration'],message='演奏器已停止。')

    def reap(self):
        if self.process is not None and not self.alive:
            code=self.process.poll()
            pending=self._pending_play
            self._pending_play=None
            self._cleanup()
            self.process=None
            self._stopping=False
            self._last_status=self._snapshot('idle',duration=self._last_status['duration'],
                                            message='演奏器已退出。' if not code else '演奏器异常退出，请重新生成曲谱后再试。')
            if pending is not None:
                self.play(pending)
