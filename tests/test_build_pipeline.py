"""Exercise the real PowerShell build flow without generating release artifacts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


POWERSHELL = shutil.which('pwsh') or shutil.which('powershell')
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'win32' and POWERSHELL, 'Requires Windows PowerShell')
class BuildPipelineTests(unittest.TestCase):
    def run_build(self, test_exit, smoke_exit=0, version=None):
        with tempfile.TemporaryDirectory(prefix='harmonica build ') as temporary:
            folder = Path(temporary)
            project = folder / 'project'
            scripts = project / 'scripts'
            scripts.mkdir(parents=True)
            for name in ('build.ps1', 'test.ps1'):
                shutil.copyfile(ROOT / 'scripts' / name, scripts / name)
            (scripts / 'smoke.ps1').write_text(
                "param($Report, $ExecutablePath)\n"
                "New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Report) | Out-Null\n"
                "Set-Content -LiteralPath $Report -Value '{}'\n"
                "& $env:HARMONICA_BUILD_TEST_PYTHON --smoke $ExecutablePath\n"
                "if ($LASTEXITCODE -ne 0) { throw 'Smoke failed' }\n", encoding='utf-8')
            released = project / 'app' / 'HarmonicaStudio' / 'HarmonicaStudio.exe'
            released.parent.mkdir(parents=True)
            released.write_bytes(b'existing release')
            calls_path = folder / 'calls.jsonl'
            shim = folder / 'fake_python.py'
            shim.write_text(
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "with Path(os.environ['HARMONICA_BUILD_TEST_LOG']).open('a', encoding='utf-8') as log:\n"
                "    log.write(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')\n"
                "if sys.argv[1:3] == ['-m', 'unittest']:\n"
                "    print('Test progress on stderr', file=sys.stderr)\n"
                "    sys.exit(int(os.environ['HARMONICA_BUILD_TEST_EXIT']))\n"
                "if sys.argv[1:3] == ['-m', 'PyInstaller']:\n"
                "    print('Build progress on stderr', file=sys.stderr)\n"
                "    work = Path(sys.argv[sys.argv.index('--workpath') + 1])\n"
                "    work.mkdir(parents=True)\n"
                "    (work/'cache.bin').write_bytes(b'cache')\n"
                "if sys.argv[1] == '--smoke':\n"
                "    sys.exit(int(os.environ['HARMONICA_BUILD_SMOKE_EXIT']))\n",
                encoding='utf-8',
            )
            python = folder / 'test python.cmd'
            python.write_text(f'@"{sys.executable}" "%~dp0fake_python.py" %*\r\n', encoding='utf-8')
            result = subprocess.run(
                [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(scripts / 'build.ps1'), '-Python', str(python), '-DistPath', 'custom output',
                 *(['-Version', version] if version else [])],
                cwd=folder,
                env=dict(os.environ, HARMONICA_BUILD_TEST_LOG=str(calls_path),
                         HARMONICA_BUILD_TEST_EXIT=str(test_exit), HARMONICA_BUILD_SMOKE_EXIT=str(smoke_exit),
                         HARMONICA_BUILD_TEST_PYTHON=str(python)),
                capture_output=True, timeout=30,
            )
            calls = [json.loads(line) for line in calls_path.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(released.read_bytes(), b'existing release')
            self.assertTrue(all(Path(call['cwd']) == project for call in calls))
            self.assertTrue((project/'verification/build.log').exists())
            self.assertEqual(list((project/'build').glob('portable-stage-*')), [])
            if test_exit == 0:
                self.assertTrue((project/'verification/portable-build-smoke.json').exists())
            return result, [call['args'] for call in calls]

    def test_failed_tests_stop_before_resources_and_packaging(self):
        result, calls = self.run_build(test_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [['-m', 'unittest', 'discover', '-s', 'tests', '-v']])

    def test_passing_tests_allow_resources_then_packaging(self):
        result, calls = self.run_build(test_exit=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[:3], [
            ['-m', 'unittest', 'discover', '-s', 'tests', '-v'],
            ['scripts/make_icon.py'],
            ['scripts/collect_licenses.py'],
        ])
        self.assertEqual(calls[3][:4], ['-m', 'PyInstaller', '--noconfirm', '--distpath'])
        self.assertIn('portable-stage-', calls[3][4])
        self.assertEqual(calls[3][5], '--workpath')
        self.assertEqual(Path(calls[3][6]), Path(calls[3][4])/'work')
        self.assertEqual(calls[3][7], 'HarmonicaStudio.spec')
        self.assertEqual(calls[4][:2], ['scripts/package_portable.py', 'prepare'])
        self.assertEqual(calls[5][0], '--smoke')
        self.assertEqual(Path(calls[5][1]).parent, Path(calls[4][2]))
        self.assertEqual(calls[6][:2], ['scripts/package_portable.py', 'install'])
        self.assertEqual(Path(calls[6][3]).parts[-2:], ('custom output', 'HarmonicaStudio'))

    def test_failed_smoke_check_prevents_installing_release(self):
        result, calls = self.run_build(test_exit=0, smoke_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1][0], '--smoke')
        self.assertFalse(any('install' in call for call in calls))

    def test_version_is_updated_before_tests_and_failed_tests_prevent_packaging(self):
        result, calls = self.run_build(test_exit=1, version='1.4.7')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [['scripts/set_version.py', '1.4.7'],
                                ['-m', 'unittest', 'discover', '-s', 'tests', '-v']])


if __name__ == '__main__':
    unittest.main()
