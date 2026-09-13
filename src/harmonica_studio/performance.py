"""Repeatable controller/Qt diagnostics, isolated from personal data and input devices."""
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
from threading import Event
import time

from . import __version__
from .metrics import MetricsRecorder, summarize

SCENARIOS = ('load', 'library', 'autosave', 'cancel')


class WorkerDelay:
    """Only waits inside JobRunner's worker; cancellation wakes it immediately."""
    def __init__(self, milliseconds=0):
        if not math.isfinite(milliseconds) or not 0 <= milliseconds <= 30_000:
            raise ValueError('后台延迟须为 0 至 30000 毫秒。')
        self.seconds = milliseconds / 1000
        self.started = Event()

    def __call__(self, kind, cancel):
        self.started.set()
        if cancel.wait(self.seconds):
            raise InterruptedError('诊断任务已取消。')


class QtLagProbe:
    """Observe real event-loop lateness; no fake clock or forced main-thread sleep."""
    def __init__(self, metrics, interval_ms=10):
        from PySide6.QtCore import QTimer, Qt
        self.metrics = metrics
        self.interval = interval_ms / 1000
        self.last = time.perf_counter()
        self.timer = QTimer()
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.sample)
        self.timer.start(interval_ms)

    def sample(self):
        now = time.perf_counter()
        self.metrics.record('ui.timer_lag', max(0, now - self.last - self.interval))
        self.last = now

    def stop(self):
        self.timer.stop()


def add_arguments(parser):
    parser.error = argument_error
    parser.add_argument('--scenario', choices=SCENARIOS, default='load')
    parser.add_argument('--notes', type=int, default=1000, help='合成音符数；曲库场景为每个文件的音符数')
    parser.add_argument('--files', type=int, default=100, help='曲库场景的合成文件数量')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--ui', action='store_true', help='运行真实 Qt 窗口及事件循环延迟探针')
    parser.add_argument('--worker-delay-ms', type=float, default=0, help='仅在后台任务启动后注入可取消延迟')
    parser.add_argument('--timeout', type=float, default=120, help='整个诊断进程的超时秒数，包含准备数据')
    parser.add_argument('--max-ui-lag-ms', type=float, help='Qt 最大停顿阈值，超过时返回失败')
    parser.add_argument('--report', type=Path, help='另存 JSON 报告；标准输出始终为 JSON')


def _print_json(report):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2))


def argument_error(message):
    report = _base_report({})
    report['error'] = dict(type='ArgumentError', message=message)
    _print_json(report)
    raise SystemExit(2)


def validate_config(config):
    if config['scenario'] not in SCENARIOS:
        raise ValueError('未知诊断场景。')
    for name, maximum in (('notes', 100_000), ('files', 5000), ('repeat', 30)):
        value = config[name]
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f'{name} 须为 1 至 {maximum} 的整数。')
    for name, low, high in (('timeout', 1, 600), ('worker_delay_ms', 0, 30_000)):
        value = config[name]
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{name} 须在 {low} 至 {high} 之间。')
    if config['worker_delay_ms'] and config['scenario'] not in ('load', 'cancel'):
        raise ValueError('后台延迟仅适用于 load 和 cancel 场景；保存和扫描目前是同步操作。')
    threshold = config.get('max_ui_lag_ms')
    if threshold is not None and (not config['ui'] or not math.isfinite(threshold) or threshold <= 0):
        raise ValueError('界面延迟阈值须为正数，并同时启用 --ui。')
    if config['scenario'] == 'library' and config['notes'] * config['files'] > 5_000_000:
        raise ValueError('曲库场景的总合成音符数不能超过 500 万。')


def _base_report(config):
    safe_config = {key: None if isinstance(value, float) and not math.isfinite(value) else value
                   for key, value in config.items()}
    return dict(schema_version=1, version=__version__, ok=False, config=safe_config,
                environment=dict(python=platform.python_version(), platform=platform.system(),
                                 machine=platform.machine(), qt_platform=os.environ.get('QT_QPA_PLATFORM', 'default')),
                mode='qt' if config.get('ui') else 'controller',
                simulated_delay=bool(config.get('worker_delay_ms')), setup_excluded=True,
                runs=[], summary={})


def run_cli(args):
    config = {name: getattr(args, name) for name in
              ('scenario', 'notes', 'files', 'repeat', 'ui', 'worker_delay_ms', 'timeout', 'max_ui_lag_ms')}
    report = _base_report(config)
    try:
        validate_config(config)
        # Parent owns the directory and removes it even when the worker times out.
        with tempfile.TemporaryDirectory(prefix='harmonica-diagnose-') as temporary:
            invocation = dict(config, temporary=temporary)
            input_path = Path(temporary) / 'request.json'
            output_path = Path(temporary) / 'result.json'
            input_path.write_text(json.dumps(invocation), encoding='utf-8')
            command = ([sys.executable, '--diagnose-worker'] if getattr(sys, 'frozen', False) else
                       [sys.executable, '-m', 'harmonica_studio.performance'])
            command.extend((str(input_path), str(output_path)))
            env = os.environ.copy()
            env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]) + os.pathsep + env.get('PYTHONPATH', '')
            env['PYTHONIOENCODING'] = 'utf-8'
            process = subprocess.run(command, capture_output=True,
                                     encoding='utf-8', env=env, timeout=config['timeout'],
                                     creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            if not output_path.is_file():
                raise RuntimeError(f'诊断进程未返回报告（退出码 {process.returncode}）。')
            report = json.loads(output_path.read_text(encoding='utf-8'))
            if process.returncode and report.get('ok'):
                raise RuntimeError('诊断进程退出异常。')
    except subprocess.TimeoutExpired:
        report['error'] = dict(type='TimeoutError', message='诊断超过总时限，已结束本次诊断进程。')
    except Exception as exc:
        report['ok'] = False
        report['error'] = dict(type=type(exc).__name__, message=str(exc))
    if args.report is not None:
        try:
            _write_report(args.report, report)
        except OSError as exc:
            report['ok'] = False
            report['error'] = dict(type=type(exc).__name__, message='无法保存诊断报告。')
    _print_json(report)
    return 0 if report['ok'] else 1


def _write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            json.dump(report, file, ensure_ascii=False, allow_nan=False, indent=2)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _notes(count):
    step = min(.1, 1000 / count)
    return [dict(pitch=60 + i % 12, start=i * step, end=(i + .8) * step, velocity=80)
            for i in range(count)]


def _fixtures(root, config):
    from .midi import write_midi
    from .project import make_project, save_project
    root.mkdir()
    music = root / 'music'
    music.mkdir()
    notes = _notes(config['notes'])
    source, project = root / 'input.mid', root / 'input.hstudio'
    if config['scenario'] == 'load':
        # Eight independent tracks exercise the real ranking and table summaries.
        tracks = []
        for index in range(min(8, len(notes))):
            write_midi(notes[index::8], source)
            tracks.append(source.read_bytes()[14:])
        source.write_bytes(b'MThd' + struct.pack('>IHHH', 6, 1, len(tracks), 500) + b''.join(tracks))
    elif config['scenario'] == 'library':
        write_midi(notes, source)
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        catalog = []
        for i in range(config['files']):
            name = f'score-{i:05d}.mid'
            (music / name).write_bytes(raw)
            catalog.append(dict(file=name, title=f'合成曲谱 {i}', sha256=digest))
        (music / 'catalog.json').write_text(json.dumps(catalog), encoding='utf-8')
    else:
        save_project(project, make_project(notes, '合成诊断曲谱'))
    return music, source, project, notes


def _run_once(root, config, app):
    from .controller import AppController
    from .diagnostic_backends import SilentScriptPlayer
    from .diagnostics import FakeAudio
    from .preferences import Preferences, save_preferences
    from .project import load_project
    music, source, project, notes = _fixtures(root, config)
    home = root / 'data'
    home.mkdir()
    save_preferences(home / 'preferences.json', Preferences(library_folder=str(music), compact=False))
    delay = WorkerDelay(config['worker_delay_ms'])
    controller = AppController(home, audio=FakeAudio(), player=SilentScriptPlayer(), before_work=delay)
    window, probe = None, None
    errors = []
    controller.subscribe(lambda event, value: errors.append(type(value[0]).__name__) if event == 'error' else None)
    deadline = time.perf_counter() + config['timeout']
    next_poll = time.perf_counter()

    def drive():
        nonlocal next_poll
        now = time.perf_counter()
        if now > deadline:
            raise TimeoutError('场景超时。')
        if app is not None:
            app.processEvents()
        elif now >= next_poll:
            controller.poll()
            next_poll = now + .1
        if errors:
            raise RuntimeError('控制层操作失败：' + errors[0])
        time.sleep(.001)

    def wait_until(predicate):
        while not predicate():
            drive()

    def drain(seconds):
        end = time.perf_counter() + seconds
        wait_until(lambda: time.perf_counter() >= end)

    try:
        if app is not None:
            from .gui import MainWindow
            window = MainWindow(controller=controller)
            window.show_error = lambda exc: None
            window.show()
        if config['scenario'] in ('autosave', 'cancel'):
            controller.open_project(project)
        drain(.04)
        metrics = MetricsRecorder()
        controller.metrics = controller.jobs.metrics = metrics
        if app is not None:
            probe = QtLagProbe(metrics)
            drain(.025)
        start = time.perf_counter()
        scenario = config['scenario']
        if scenario == 'load':
            controller.load_file(source)
            wait_until(lambda: not controller.busy)
            if sum(map(len, controller.state.parts.values())) != config['notes']:
                raise AssertionError('加载后的音符数量不一致。')
        elif scenario == 'library':
            controller.refresh_library()
            if len(controller.library) != config['files']:
                raise AssertionError('扫描后的曲库数量不一致。')
        elif scenario == 'autosave':
            edited = [dict(note) for note in notes]
            edited[0]['pitch'] = 72
            if window is not None:
                window.roll._commit(edited)
                wait_until(lambda: (home / 'autosave.hstudio').exists())
            else:
                controller.replace_notes(edited)
                controller.autosave()
        else:
            controller.listen()
            wait_until(delay.started.is_set)
            with metrics.measure('scenario.stop_and_cancel'):
                controller.stop_preview()
                controller.jobs.cancel()
            wait_until(lambda: not controller.busy)
            if controller.state.result is not None or controller.audio.playing or controller.player.alive:
                raise AssertionError('取消后仍接收了结果或开始播放。')
            if controller.state.project['notes'] != notes:
                raise AssertionError('取消操作修改了曲谱。')
        elapsed = time.perf_counter() - start
        # Allow the timer to observe a synchronous block before stopping the probe.
        drain(.03)
        if probe is not None:
            probe.stop()
        snapshot = metrics.snapshot()
        controller.metrics = controller.jobs.metrics = None
        if scenario == 'autosave':
            if load_project(home / 'autosave.hstudio')['notes'] != edited or not controller.state.project_dirty:
                raise AssertionError('自动保存的内容或手动保存状态错误。')
        if snapshot['dropped_samples']:
            raise RuntimeError('测量样本超出容量，请缩短单次诊断时间。')
        return dict(ok=True, elapsed_ms=elapsed * 1000, metrics=snapshot)
    finally:
        if probe is not None:
            probe.stop()
        controller.metrics = controller.jobs.metrics = None
        try:
            controller.close()
        finally:
            controller.jobs.close()
            if window is not None:
                from PySide6.QtCore import QCoreApplication, QEvent
                window.close()
                window.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                app.processEvents()
            controller.jobs.executor.shutdown(wait=True, cancel_futures=True)


def run_worker(config):
    root = Path(config.pop('temporary'))
    validate_config(config)
    report = _base_report(config)
    app = None
    try:
        if config['ui']:
            import PySide6
            from PySide6.QtWidgets import QApplication
            from .theme import STYLE
            app = QApplication.instance() or QApplication([])
            app.setQuitOnLastWindowClosed(False)
            app.setStyle('Fusion')
            app.setStyleSheet(STYLE)
            report['environment']['pyside6'] = PySide6.__version__
        for index in range(config['repeat']):
            report['runs'].append(_run_once(root / f'run-{index}', config, app))
        grouped = {}
        for run in report['runs']:
            for sample in run['metrics']['samples']:
                grouped.setdefault(sample['name'], []).append(sample['duration_ms'])
        report['summary'] = {name: summarize(values) for name, values in grouped.items()}
        report['elapsed'] = summarize([run['elapsed_ms'] for run in report['runs']])
        threshold = config.get('max_ui_lag_ms')
        lag = report['summary'].get('ui.timer_lag', {}).get('max_ms')
        if config['ui'] and lag is None:
            raise RuntimeError('没有获得 Qt 延迟样本。')
        if threshold is not None and lag > threshold:
            raise AssertionError('Qt 最大事件循环延迟超过指定阈值。')
        report['ok'] = True
    except Exception as exc:
        report['error'] = dict(type=type(exc).__name__, message=str(exc).replace(str(root), '<temporary>'))
    return report


def worker_main():
    from contextlib import redirect_stdout
    # A windowed portable executable has no Python stdin/stdout streams.
    input_path, output_path = map(Path, sys.argv[-2:])
    config = json.loads(input_path.read_text(encoding='utf-8'))
    with redirect_stdout(sys.stderr):
        report = run_worker(config)
    _write_report(output_path, report)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(worker_main())
