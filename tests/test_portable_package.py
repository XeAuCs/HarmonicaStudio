import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('package_portable',
    Path(__file__).resolve().parents[1] / 'scripts/package_portable.py')
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PortablePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'staged'
        (self.source / '_internal').mkdir(parents=True)
        (self.source / 'HarmonicaStudio.exe').write_bytes(b'new executable')
        (self.source / '_internal/runtime.dll').write_bytes(b'new runtime')
        self.samples = self.root / 'seed'
        self.samples.mkdir()
        (self.samples / 'sample.mid').write_bytes(b'bundled song')
        package.prepare(self.source, self.samples)
        self.target = self.root / 'installed'

    def test_fresh_release_has_one_external_library_and_empty_data(self):
        package.install(self.source, self.target)
        self.assertEqual((self.target/'samples/sample.mid').read_bytes(), b'bundled song')
        self.assertEqual(list((self.target/'data').iterdir()), [])
        self.assertFalse((self.target/'_internal/samples').exists())

    def test_update_preserves_data_custom_songs_and_user_deletions(self):
        package.install(self.source, self.target)
        (self.target/'samples/sample.mid').unlink()
        (self.target/'samples/自己的歌.mid').write_bytes(b'personal song')
        (self.target/'data/autosave.hstudio').write_bytes(b'unsaved project')
        (self.target/'data/preferences.json').write_bytes(b'personal settings')
        (self.target/'_internal/obsolete.dll').write_bytes(b'old dependency')
        (self.source/'HarmonicaStudio.exe').write_bytes(b'updated executable')
        package.install(self.source, self.target)
        self.assertEqual((self.target/'HarmonicaStudio.exe').read_bytes(), b'updated executable')
        self.assertEqual((self.target/'samples/自己的歌.mid').read_bytes(), b'personal song')
        self.assertFalse((self.target/'samples/sample.mid').exists())
        self.assertEqual((self.target/'data/autosave.hstudio').read_bytes(), b'unsaved project')
        self.assertEqual((self.target/'data/preferences.json').read_bytes(), b'personal settings')
        self.assertFalse((self.target/'_internal/obsolete.dll').exists())
        self.assertFalse(list(self.root.glob('.portable-*')))

    def test_failed_replacement_restores_previous_program_and_data(self):
        package.install(self.source, self.target)
        (self.target/'data/autosave.hstudio').write_bytes(b'precious project')
        (self.source/'HarmonicaStudio.exe').write_bytes(b'next version')
        rename = Path.rename

        def fail_install(path, target):
            if path.name.startswith('.portable-install-'):
                raise PermissionError('simulated update failure')
            return rename(path, target)

        with patch.object(Path, 'rename', fail_install), self.assertRaises(PermissionError):
            package.install(self.source, self.target)
        self.assertEqual((self.target/'HarmonicaStudio.exe').read_bytes(), b'new executable')
        self.assertEqual((self.target/'data/autosave.hstudio').read_bytes(), b'precious project')
        self.assertFalse(list(self.root.glob('.portable-*')))

    def test_overlapping_install_paths_are_rejected_without_touching_source(self):
        with self.assertRaises(ValueError):
            package.install(self.source, self.source/'nested')
        self.assertEqual((self.source/'HarmonicaStudio.exe').read_bytes(), b'new executable')
