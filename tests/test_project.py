import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import wave

from harmonica_studio.midi import read_midi, write_midi
from harmonica_studio.models import Options
from harmonica_studio.preview import decode_events
from harmonica_studio.project import make_project, validate_project, save_project, load_project
from harmonica_studio.service import convert, export_project


def note(pitch=60, start=0, end=.2):
    return dict(pitch=pitch, start=start, end=end, velocity=80)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='harmonica-project-test-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip_is_self_contained_after_source_removed(self):
        source = self.root/'source.mid'
        write_midi([note(), note(62, .3, .5)], source)
        first, _ = convert(source, self.root/'exports')
        project = load_project(first/'工程.hstudio')
        source.unlink()
        save_project(self.root/'saved.hstudio', project)
        restored = load_project(self.root/'saved.hstudio')
        second, report = export_project(restored, self.root/'exports')
        self.assertEqual(project, restored)
        self.assertEqual(project['source']['path'], str(source))
        self.assertTrue(report['edited'])
        self.assertEqual(report['melody_notes'], 2)
        self.assertEqual((first/'按键时间表.json').read_bytes(), (second/'按键时间表.json').read_bytes())

    def test_validation_produces_sorted_deep_copy(self):
        notes = [note(64, .3, .5), note()]
        project = make_project(notes, '  我的曲谱  ', options=Options(), report={'extra': {'value': 1}})
        normalized = validate_project(project)
        normalized['report']['extra']['value'] = 2
        normalized['notes'][0]['pitch'] = 65
        self.assertEqual(project['report']['extra']['value'], 1)
        self.assertEqual(project['notes'][0]['pitch'], 60)
        self.assertEqual(notes[0]['pitch'], 64)
        self.assertEqual(project['title'], '我的曲谱')
        self.assertEqual(project['options']['speed'], 1.0)

    def test_empty_project_can_be_saved_but_not_exported(self):
        project = make_project([])
        save_project(self.root/'empty.hstudio', project)
        self.assertEqual(load_project(self.root/'empty.hstudio')['notes'], [])
        with self.assertRaisesRegex(ValueError, '还没有音符'):
            export_project(project, self.root/'exports')

    def test_corrupt_or_unsupported_project_rejected(self):
        path = self.root/'corrupt.hstudio'
        for data in ('{bad', '[]', '{"schema_version":2,"notes":[]}', '{"schema_version":true,"notes":[]}',
                     '{"schema_version":1,"notes":[{"pitch":60,"start":NaN,"end":1}]}'):
            with self.subTest(data=data):
                path.write_text(data, encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_project(path)

    def test_invalid_note_fields_and_overlaps_rejected(self):
        for fields in ({'pitch':47}, {'pitch':86}, {'pitch':True}, {'velocity':0}, {'velocity':128},
                       {'start':-.1}, {'start':float('nan')}, {'end':float('inf')}, {'end':1201},
                       {'start':10**500}, {'end':0}, {'start':'0'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                make_project([dict(note(), **fields)])
        with self.assertRaisesRegex(ValueError, '重叠'):
            make_project([note(), note(64, .1, .3)])
        self.assertEqual(len(make_project([note(), note(64, .2, .3)])['notes']), 2)

    def test_invalid_metadata_cannot_overwrite_previous_save(self):
        path = self.root/'saved.hstudio'
        save_project(path, make_project([note()]))
        original = path.read_bytes()
        project = make_project([note()])
        project['report'] = {'bad': float('nan')}
        with self.assertRaises(ValueError):
            save_project(path, project)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.glob('.*.tmp')), [])

    def test_float_boundary_normalization_is_idempotent_and_preserves_other_times(self):
        original = [note(60, 1.4, 1.7000000000000002), note(62, 1.7, 2.012345678901)]
        project = make_project(original)
        self.assertEqual(project['notes'][0]['end'], 1.7)
        self.assertEqual(project['notes'][1], original[1])
        self.assertEqual(validate_project(project), project)
        self.assertEqual(original[0]['end'], 1.7000000000000002)
        with self.assertRaises(ValueError):
            make_project([note(60, 0, 1e-9), note(62, 0, 1e-9)])
        with self.assertRaises(ValueError):
            make_project([note(60, 0, .2001), note(62, .2, .4)])

    def test_file_size_and_note_count_limits(self):
        path = self.root/'large.hstudio'
        path.write_bytes(b' ' * 513)
        with patch('harmonica_studio.project.MAX_FILE_BYTES', 512):
            with self.assertRaisesRegex(ValueError, '20 MB'):
                load_project(path)
        with patch('harmonica_studio.project.MAX_NOTES', 1):
            with self.assertRaisesRegex(ValueError, '音符列表'):
                make_project([note(), note(62, .3, .5)])

    def test_edit_changes_exported_midi_events_audio_and_report(self):
        original = make_project([note(), note(62, .3, .5)], report={'melody_notes': 999})
        before = copy.deepcopy(original)
        first, _ = export_project(original, self.root/'exports')
        edited = copy.deepcopy(original)
        edited['notes'][0]['pitch'] = 73
        edited['notes'].append(note(64, .6, .8))
        second, report = export_project(edited, self.root/'exports')
        events = json.loads((second/'按键时间表.json').read_text('utf-8'))
        parts, _ = read_midi(second/'口琴单旋律.mid')
        self.assertEqual([n['pitch'] for n in decode_events(events)], [73, 62, 64])
        self.assertEqual([n['pitch'] for n in parts[(0, 0)]], [73, 62, 64])
        self.assertEqual(report['melody_notes'], 3)
        self.assertEqual(report['current_notes'], 3)
        self.assertNotEqual((first/'试听.wav').read_bytes(), (second/'试听.wav').read_bytes())
        with wave.open(str(second/'试听.wav')) as wav:
            self.assertAlmostEqual(wav.getnframes()/wav.getframerate(), report['duration_seconds'] + 1/3, places=3)
        self.assertEqual(original, before)

    def test_repeated_exports_do_not_accumulate_lead_in_or_key_spacing(self):
        project = make_project([note(61, 0, .01), note(73, .01, .02), note(60, .03, .04)])
        canonical = copy.deepcopy(project['notes'])
        first, _ = export_project(project, self.root/'exports')
        restored = load_project(first/'工程.hstudio')
        second, _ = export_project(restored, self.root/'exports')
        again = load_project(second/'工程.hstudio')
        third, _ = export_project(again, self.root/'exports')
        self.assertEqual(restored['notes'], canonical)
        self.assertEqual(again['notes'], canonical)
        for filename in ('按键时间表.json', '音符.json', '口琴单旋律.mid', '试听.wav'):
            self.assertEqual((first/filename).read_bytes(), (third/filename).read_bytes())

    def test_cancelled_project_export_cleans_transaction(self):
        cancel = threading.Event()
        def cancel_during_render(events, path, token):
            path.write_bytes(b'incomplete')
            token.set()
        with patch('harmonica_studio.service.render_wav', side_effect=cancel_during_render):
            with self.assertRaises(InterruptedError):
                export_project(make_project([note()]), self.root/'exports', cancel=cancel)
        self.assertEqual(list((self.root/'exports').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
