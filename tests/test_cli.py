"""Public command-line contracts, using synthetic MIDI and isolated output."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from harmonica_studio.midi import write_midi
from harmonica_studio.project import load_project


class CLITests(unittest.TestCase):
    def test_inspect_and_convert_keep_json_and_complete_export_contracts(self):
        with tempfile.TemporaryDirectory(prefix='harmonica-cli-') as temporary:
            root = Path(temporary)
            source = root / 'input.mid'
            write_midi([dict(pitch=60, start=0, end=.1, velocity=80)], source)
            env = dict(os.environ, PYTHONIOENCODING='utf-8', HARMONICA_STUDIO_HOME=str(root / 'unused'))

            def invoke(*arguments):
                process = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / 'launch.py'),
                                          *arguments], capture_output=True, encoding='utf-8', env=env, timeout=10)
                self.assertEqual(process.returncode, 0, process.stderr)
                return json.loads(process.stdout)

            parts = invoke('inspect', str(source))
            self.assertEqual(parts[0]['notes'], 1)
            self.assertIn('recommendation_score', parts[0])
            result = invoke('convert', str(source), '--out', str(root / 'exports'))
            self.assertEqual(set(result), {'folder', 'report'})
            folder = Path(result['folder'])
            self.assertEqual(folder.parent, root / 'exports')
            self.assertEqual({path.name for path in folder.iterdir()}, {
                '工程.hstudio', '音符.json', '按键时间表.json', '转换报告.json',
                '演奏脚本.ahk', '口琴单旋律.mid', '试听.wav'})
            self.assertEqual(load_project(folder / '工程.hstudio')['notes'][0]['pitch'], 60)
            self.assertFalse((root / 'unused').exists())
