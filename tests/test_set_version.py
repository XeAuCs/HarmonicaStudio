import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('set_version', ROOT/'scripts/set_version.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SetVersionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contents = {
            'src/harmonica_studio/__init__.py': "__version__ = '1.4.7'\r\n",
            'pyproject.toml': '[project]\r\nversion = "1.4.7"\r\n',
            'README.md': '当前版本：**1.4.7**。\r\n历史版本 1.4.7 保持原样。\r\n',
        }
        for name, text in self.contents.items():
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode())

    def test_updates_all_current_versions_without_changing_history_or_line_endings(self):
        module.set_version(self.root, '1.5.0')
        for name, text in self.contents.items():
            expected = text.replace('1.4.7', '1.5.0', 1).encode()
            self.assertEqual((self.root/name).read_bytes(), expected)

    def test_invalid_version_changes_nothing(self):
        for version in ('1.4', '01.4.7', '1.4.7;whoami', '-1.4.7', '1.4.7\n'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                module.set_version(self.root, version)
        for name, text in self.contents.items():
            self.assertEqual((self.root/name).read_bytes(), text.encode())

    def test_missing_declaration_is_detected_before_any_file_is_changed(self):
        (self.root/'README.md').write_bytes(b'no version declaration')
        with self.assertRaises(ValueError):
            module.set_version(self.root, '1.5.0')
        for name, text in self.contents.items():
            if name != 'README.md':
                self.assertEqual((self.root/name).read_bytes(), text.encode())
