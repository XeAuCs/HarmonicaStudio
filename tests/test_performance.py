"""Diagnostic contracts: real workflows, deterministic timing and isolated CLI runs."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest

from harmonica_studio.app_state import FollowUp
from harmonica_studio.controller import AppController
from harmonica_studio.diagnostic_backends import ControlledExecutor, SilentScriptPlayer
from harmonica_studio.diagnostics import FakeAudio
from harmonica_studio.jobs import JobKind, JobRunner
from harmonica_studio.metrics import MetricsRecorder, summarize
from harmonica_studio.performance import WorkerDelay
from harmonica_studio.project import make_project, save_project

ROOT = Path(__file__).resolve().parents[1]


class MetricsTests(unittest.TestCase):
    def test_nested_failed_spans_keep_original_exception_and_do_not_sum_children(self):
        now = [0.0]
        metrics = MetricsRecorder(now=lambda: now[0])
        error = ValueError('private document contents')
        with self.assertRaises(ValueError) as caught:
            with metrics.measure('outer', revision=3):
                now[0] = .01
                with metrics.measure('inner'):
                    now[0] = .03
                    raise error
        self.assertIs(caught.exception, error)
        result = metrics.snapshot()
        self.assertAlmostEqual(result['summary']['outer']['max_ms'], 30)
        self.assertAlmostEqual(result['summary']['inner']['max_ms'], 20)
        self.assertEqual(result['samples'][1]['outcome'], 'error')
        self.assertNotIn('private', json.dumps(result))

    def test_samples_are_bounded_thread_safe_and_return_independent_snapshots(self):
        metrics = MetricsRecorder(max_samples=20)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: metrics.record('work', .01), range(100)))
        snapshot = metrics.snapshot()
        self.assertEqual(len(snapshot['samples']), 20)
        self.assertEqual(snapshot['dropped_samples'], 80)
        snapshot['samples'][0]['name'] = 'changed'
        self.assertEqual(metrics.snapshot()['samples'][0]['name'], 'work')
        self.assertEqual(summarize([])['max_ms'], None)
        self.assertEqual(summarize(list(range(1, 101)))['p95_ms'], 95)

    def test_job_separates_queue_execution_delivery_and_keeps_result(self):
        now = [0.0]
        metrics = MetricsRecorder(now=lambda: now[0])
        executor = ControlledExecutor()
        jobs = JobRunner(executor, metrics=metrics)
        self.addCleanup(jobs.close)
        def operation(cancel):
            now[0] = .05
            return 'result'
        jobs.start(JobKind.CONVERT, 4, operation)
        now[0] = .02
        executor.finish_next()
        now[0] = .08
        job = jobs.take_completed()
        self.assertEqual(job.future.result(), 'result')
        summary = metrics.snapshot()['summary']
        for name, expected in (('job.queue', 20), ('job.execute', 30), ('job.delivery', 30)):
            self.assertAlmostEqual(summary[name]['max_ms'], expected)

    def test_worker_delay_is_cancelled_without_running_operation_even_without_metrics(self):
        delay = WorkerDelay(30_000)
        called = Event()
        jobs = JobRunner(before_work=delay)
        self.addCleanup(jobs.close)
        job = jobs.start(JobKind.LOAD, 1, lambda: called.set(), cancellable=False)
        self.assertTrue(delay.started.wait(2))
        jobs.cancel()
        with self.assertRaises(InterruptedError):
            job.future.result(timeout=2)
        self.assertFalse(called.is_set())
        self.assertEqual(job.follow_up, FollowUp.NONE)

    def test_controller_reports_stale_result_without_installing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            metrics, executor = MetricsRecorder(), ControlledExecutor()
            controller = AppController(temporary, audio=FakeAudio(), player=SilentScriptPlayer(),
                                       executor=executor, metrics=metrics)
            try:
                project = make_project([dict(pitch=60, start=0, end=.02)])
                path = Path(temporary) / 'input.hstudio'
                save_project(path, project)
                controller.open_project(path)
                controller.listen()
                controller.state.set_project(make_project([], 'replacement'))
                executor.finish_next()
                controller.poll()
                self.assertIsNone(controller.state.result)
                self.assertFalse(controller.audio.playing)
                outcomes = [s['outcome'] for s in metrics.snapshot()['samples'] if s['name'] == 'job.result']
                self.assertEqual(outcomes, ['stale'])
            finally:
                controller.close()


class DiagnoseCLITests(unittest.TestCase):
    def invoke(self, *arguments):
        env = os.environ.copy()
        env['QT_QPA_PLATFORM'] = 'offscreen'
        result = subprocess.run([sys.executable, str(ROOT / 'launch.py'), 'diagnose', *arguments],
                                capture_output=True, encoding='utf-8', env=env, timeout=20)
        try:
            report = json.loads(result.stdout)
        except ValueError:
            self.fail(f'CLI did not emit JSON: {result.stdout}\n{result.stderr}')
        return result.returncode, report

    def test_load_uses_real_controller_and_structured_metrics(self):
        code, report = self.invoke('--scenario', 'load', '--notes', '37', '--repeat', '2')
        self.assertEqual(code, 0, report)
        self.assertTrue(report['ok'])
        self.assertEqual(report['schema_version'], 1)
        self.assertFalse(report['simulated_delay'])
        self.assertEqual(report['summary']['controller.install_parts']['count'], 2)
        self.assertEqual(report['summary']['job.execute']['count'], 2)
        self.assertNotIn('ui.timer_lag', report['summary'])

    def test_export_measures_worker_and_result_installation(self):
        code, report = self.invoke('--scenario', 'export', '--notes', '12', '--repeat', '2')
        self.assertEqual(code, 0, report)
        self.assertTrue(report['ok'])
        for metric in ('controller.show_result', 'controller.autosave'):
            self.assertEqual(report['summary'][metric]['count'], 2)
        self.assertEqual(report['summary']['job.execute']['count'], 4)
        for run in report['runs']:
            outcomes = [(s['kind'], s['outcome']) for s in run['metrics']['samples'] if s['name'] == 'job.result']
            self.assertEqual(outcomes, [('export', 'ok'), ('save', 'ok')])

    def test_library_and_autosave_use_temporary_data_not_external_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sentinel = root / 'autosave.hstudio'
            sentinel.write_text('personal data', encoding='utf-8')
            from unittest.mock import patch
            with patch.dict(os.environ, HARMONICA_STUDIO_HOME=str(root)):
                for scenario in ('library', 'autosave'):
                    with self.subTest(scenario=scenario):
                        code, report = self.invoke('--scenario', scenario, '--notes', '12',
                                                   '--files', '3', '--repeat', '1')
                        self.assertEqual(code, 0, report)
                        if scenario == 'library':
                            self.assertEqual(report['summary']['controller.install_library']['count'], 1)
                            self.assertEqual(report['summary']['job.execute']['count'], 1)
                            outcomes = [s for s in report['runs'][0]['metrics']['samples'] if s['name'] == 'job.result']
                            self.assertEqual([(s['kind'], s['outcome']) for s in outcomes], [('library', 'ok')])
            self.assertEqual(sentinel.read_text(encoding='utf-8'), 'personal data')
            self.assertEqual(list(root.iterdir()), [sentinel])

    def test_qt_library_waits_for_requested_scan_and_excludes_startup_scan(self):
        try:
            import PySide6
        except ImportError:
            self.skipTest('PySide6 unavailable; Qt diagnostic not verified')
        code, report = self.invoke('--scenario', 'library', '--notes', '12', '--files', '3',
                                   '--repeat', '2', '--ui')
        self.assertEqual(code, 0, report)
        for metric in ('controller.refresh_library', 'controller.install_library', 'job.execute'):
            self.assertEqual(report['summary'][metric]['count'], 2)

    def test_cancellation_interrupts_injected_delay_and_never_autoplays(self):
        code, report = self.invoke('--scenario', 'cancel', '--notes', '10', '--repeat', '1',
                                   '--worker-delay-ms', '30000', '--timeout', '10')
        self.assertEqual(code, 0, report)
        self.assertTrue(report['simulated_delay'])
        result = [s for s in report['runs'][0]['metrics']['samples'] if s['name'] == 'job.result']
        self.assertEqual(result[0]['outcome'], 'cancelled')

    def test_timeout_is_nonzero_json_and_does_not_wait_for_injected_delay(self):
        code, report = self.invoke('--scenario', 'load', '--notes', '10', '--repeat', '1',
                                   '--worker-delay-ms', '30000', '--timeout', '1')
        self.assertNotEqual(code, 0)
        self.assertFalse(report['ok'])
        self.assertEqual(report['error']['type'], 'TimeoutError')

    def test_invalid_nonfinite_parameters_and_inapplicable_delay_are_json_errors(self):
        for options in (('--notes', '0'), ('--worker-delay-ms', 'nan'),
                        ('--max-ui-lag-ms', 'nan'), ('--scenario', 'autosave', '--worker-delay-ms', '2')):
            with self.subTest(options=options):
                code, report = self.invoke(*options)
                self.assertNotEqual(code, 0)
                self.assertFalse(report['ok'])
                self.assertEqual(report['error']['type'], 'ValueError')

    def test_unknown_options_and_bad_argument_types_are_json_errors(self):
        for options in (('--unknown',), ('--notes', 'bad'), ('--scenario', 'bad')):
            with self.subTest(options=options):
                code, report = self.invoke(*options)
                self.assertEqual(code, 2)
                self.assertEqual(report['error']['type'], 'ArgumentError')

    def test_qt_autosave_exercises_editor_debounce_and_event_loop_probe(self):
        try:
            import PySide6
        except ImportError:
            self.skipTest('PySide6 unavailable; Qt diagnostic not verified')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'report.json'
            code, report = self.invoke('--scenario', 'autosave', '--notes', '100', '--repeat', '1',
                                       '--ui', '--report', str(path))
            self.assertEqual(code, 0, report)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), report)
            self.assertEqual(report['mode'], 'qt')
            self.assertGreater(report['summary']['ui.timer_lag']['count'], 0)
            self.assertEqual(report['summary']['controller.autosave']['count'], 1)
            self.assertEqual(report['summary']['controller.replace_notes']['count'], 1)
            saved = [s for s in report['runs'][0]['metrics']['samples']
                     if s['name'] == 'job.result' and s['kind'] == 'save']
            self.assertEqual([(s['save_kind'], s['outcome']) for s in saved], [('auto', 'ok')])


if __name__ == '__main__':
    unittest.main()
