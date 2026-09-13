import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
from harmonica_studio.library import sample_entries
from harmonica_studio.preferences import Preferences, load_preferences, save_preferences
from harmonica_studio.paths import default_library_root, data_root, library_path, library_setting


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_strict_scan_distinguishes_failure_from_missing_directory(self):
        with patch.object(Path, 'iterdir', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                sample_entries(self.root, strict=True)
        self.assertEqual(sample_entries(self.root / 'missing', strict=True), [])

    def test_hashing_checks_cancellation_between_chunks(self):
        path = self.root / 'large.mid'
        path.write_bytes(b'x' * (2 * 1024 * 1024))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.root / 'catalog.json').write_text(json.dumps([dict(file=path.name, sha256=digest)]), encoding='utf-8')
        cancel, reads = Event(), []
        original_open = Path.open

        class CancelAfterRead:
            def __init__(self, file):
                self.file = file

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.file.close()

            def read(self, size):
                reads.append(size)
                raw = self.file.read(size)
                cancel.set()
                return raw

        def open_file(candidate, *args, **kwargs):
            file = original_open(candidate, *args, **kwargs)
            return CancelAfterRead(file) if candidate == path else file

        with patch.object(Path, 'open', open_file):
            with self.assertRaises(InterruptedError):
                sample_entries(self.root, cancel, strict=True)
        self.assertEqual(reads, [64 * 1024])

    def test_files_appear_and_disappear_without_catalog_edits(self):
        (self.root/'新曲.MIDI').write_bytes(b'new')
        (self.root/'伴奏.kar').write_bytes(b'kar')
        (self.root/'说明.txt').write_text('not a song')
        (self.root/'子目录').mkdir();(self.root/'子目录/隐藏.mid').write_bytes(b'child')
        self.assertEqual({e['file'] for e in sample_entries(self.root)}, {'新曲.MIDI','伴奏.kar'})
        (self.root/'新曲.MIDI').unlink()
        self.assertEqual([e['file'] for e in sample_entries(self.root)], ['伴奏.kar'])

    def test_catalog_only_enriches_matching_original_bytes(self):
        path=self.root/'旋律.mid';path.write_bytes(b'original MIDI bytes')
        metadata=[dict(file=path.name,title='曲名',sha256=hashlib.sha256(path.read_bytes()).hexdigest(),options={'track':1,'channel':1}),
                  dict(file='missing.mid',title='未下载')]
        (self.root/'catalog.json').write_text(json.dumps(metadata),encoding='utf-8')
        self.assertEqual(sample_entries(self.root)[0]['options'], {'track':1,'channel':1})
        path.write_bytes(b'a different arrangement')
        entries=sample_entries(self.root)
        self.assertEqual(len(entries),1)
        self.assertNotIn('options',entries[0])
        self.assertEqual(entries[0]['title'],'旋律')

    def test_broken_catalog_still_lists_files_and_missing_folder_is_empty(self):
        (self.root/'catalog.json').write_text('{broken',encoding='utf-8')
        (self.root/'real.rmi').write_bytes(b'raw')
        self.assertEqual(sample_entries(self.root)[0]['file'],'real.rmi')
        self.assertEqual(sample_entries(self.root/'missing'),[])

    def test_packaged_paths_follow_executable_even_under_app_or_after_moving(self):
        for folder in (self.root/'app/HarmonicaStudio', self.root/'移动后的目录'):
            with self.subTest(folder=folder), patch.dict('os.environ', {}, clear=True), \
                    patch('sys.frozen',True,create=True), patch('sys.executable',str(folder/'HarmonicaStudio.exe')):
                self.assertEqual(default_library_root(), folder/'samples')
                self.assertEqual(data_root(), folder/'data')
                self.assertEqual(library_setting(str(folder/'samples')), '')
                self.assertEqual(library_setting(str(folder/'个人曲库')), '个人曲库')
                self.assertEqual(library_path('个人曲库'), folder/'个人曲库')
                self.assertEqual(library_path(''), folder/'samples')

    def test_explicit_external_data_and_library_locations_remain_supported(self):
        external = self.root/'外部数据'
        with patch.dict('os.environ', {'HARMONICA_STUDIO_HOME':str(external)}):
            self.assertEqual(data_root(), external)
        self.assertEqual(library_path(str(external)), external)

    def test_preferences_survive_restart(self):
        path=self.root/'preferences.json'
        expected=Preferences('blue',True,str(self.root/'曲谱'))
        save_preferences(path,expected)
        self.assertEqual(load_preferences(path),expected)

    def test_invalid_preferences_fall_back_field_by_field(self):
        path=self.root/'preferences.json'
        path.write_text(json.dumps({'theme':'unknown','compact':'false','library_folder':123}),encoding='utf-8')
        self.assertEqual(load_preferences(path),Preferences())

    def test_long_rest_preference_defaults_on_and_explicit_false_survives(self):
        path=self.root/'preferences.json'
        for raw in ({}, {'skip_long_rests':'false'}):
            path.write_text(json.dumps(raw),encoding='utf-8')
            self.assertTrue(load_preferences(path).skip_long_rests)
        expected=Preferences(skip_long_rests=False)
        save_preferences(path,expected)
        self.assertEqual(load_preferences(path),expected)
        path.write_text('{broken',encoding='utf-8')
        self.assertEqual(load_preferences(path),Preferences())
