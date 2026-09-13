from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
import wave

from harmonica_studio.midi import read_midi
from harmonica_studio.preview import decode_events
from harmonica_studio.project import make_project, load_project
from harmonica_studio.rests import compress_long_rests
from harmonica_studio.schedule import build_events
from harmonica_studio.service import export_project
from harmonica_studio.transport import playback_anchors, TimeMap


def note(start, end, pitch=60):
    return dict(start=start, end=end, pitch=pitch, velocity=80)


class LongRestTests(unittest.TestCase):
    def test_only_long_internal_silence_changes_and_input_is_preserved(self):
        notes = [note(7, 17), note(20, 20.8, 62), note(25, 26, 64),
                 note(26.1, 26.4, 65), note(32.4, 33.4, 67)]
        original = deepcopy(notes)
        shifted, count, saved = compress_long_rests(notes, True)
        self.assertEqual(notes, original)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(saved, 9)
        self.assertEqual(shifted[0], notes[0])
        self.assertEqual(shifted[1], notes[1])
        for source, result in zip(notes, shifted):
            self.assertAlmostEqual(result['end'] - result['start'], source['end'] - source['start'])
            self.assertEqual(result['pitch'], source['pitch'])
            self.assertEqual(result['velocity'], source['velocity'])
        for index, gap in enumerate((3, .6, .1, .6), 1):
            self.assertAlmostEqual(shifted[index]['start'] - shifted[index-1]['end'], gap)
        shifted[0]['pitch'] = 72
        self.assertEqual(notes, original)

    def test_three_second_boundary_tolerates_floating_point_arithmetic(self):
        for gap, expected in ((2.999999, 0), (3, 0), (3 + 5e-9, 0), (3 + 1e-7, 1)):
            with self.subTest(gap=gap):
                notes = [note(.2, .5), note(.5 + gap, 1 + gap)]
                shifted, count, saved = compress_long_rests(notes, True)
                self.assertEqual(count, expected)
                self.assertAlmostEqual(saved, gap - .6 if expected else 0)
                self.assertAlmostEqual(shifted[1]['start'] - shifted[0]['end'], .6 if expected else gap)

    def test_off_empty_and_single_note_preserve_timing(self):
        for notes, enabled in (([], True), ([note(10, 25)], True),
                               ([note(0, 1), note(20, 21)], False)):
            with self.subTest(notes=notes, enabled=enabled):
                shifted, count, saved = compress_long_rests(notes, enabled)
                self.assertEqual((shifted, count, saved), (notes, 0, 0))
                self.assertIsNot(shifted, notes)
                if notes:self.assertIsNot(shifted[0], notes[0])
        self.assertEqual(compress_long_rests([note(0, 1), note(20, 21)])[1:], (0, 0))

    def test_invalid_export_option_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            for value in (None, 0, 1, 'true', [], {}):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, '布尔值'):
                    export_project(make_project([note(0, .1)], options={'skip_long_rests': value}), temp)
            self.assertEqual(list(Path(temp).iterdir()), [])


class LongRestExportTests(unittest.TestCase):
    def test_all_playback_outputs_shorten_together_and_switching_off_restores_timing(self):
        with tempfile.TemporaryDirectory() as temp:
            notes = [note(.25, 1.25), note(5.25, 6.25, 73), note(10.25, 10.5, 64)]
            project = make_project(notes, '长空白验证', options={'skip_long_rests': True})
            original = deepcopy(project)
            first, report = export_project(project, temp)
            self.assertEqual(project, original)
            self.assertTrue(report['skip_long_rests'])
            self.assertEqual(report['skipped_long_rests'], 2)
            self.assertAlmostEqual(report['removed_rest_seconds'], 6.8)
            events = json.loads((first/'按键时间表.json').read_text('utf-8'))
            actual = json.loads((first/'音符.json').read_text('utf-8'))
            self.assertEqual(decode_events(events), actual)
            self.assertEqual(read_midi(first/'口琴单旋律.mid')[0][(0, 0)], actual)
            script = (first/'演奏脚本.ahk').read_text('utf-8-sig')
            for event in events:
                self.assertIn(','.join(map(str, event)), script)
            for index in (1, 2):
                self.assertAlmostEqual(actual[index]['start'] - actual[index-1]['end'], .6)
            for source, played in zip(notes, actual):
                self.assertAlmostEqual(source['end'] - source['start'], played['end'] - played['start'])
            with wave.open(str(first/'试听.wav')) as wav:
                self.assertAlmostEqual(wav.getnframes()/wav.getframerate(), report['duration_seconds'] + 1/3, places=3)
                wav.setpos(round((actual[0]['end'] + .2) * wav.getframerate()))
                self.assertEqual(wav.readframes(100), b'\0\0' * 100)
            restored = load_project(first/'工程.hstudio')
            self.assertEqual(restored['notes'], notes)
            second, repeat_report = export_project(restored, temp)
            self.assertEqual(repeat_report['removed_rest_seconds'], report['removed_rest_seconds'])
            for filename in ('按键时间表.json', '音符.json', '口琴单旋律.mid', '试听.wav'):
                self.assertEqual((first/filename).read_bytes(), (second/filename).read_bytes())
            restored['options']['skip_long_rests'] = False
            third, off_report = export_project(restored, temp)
            full = json.loads((third/'音符.json').read_text('utf-8'))
            self.assertEqual(full, decode_events(build_events(notes)[0]))
            self.assertEqual(load_project(third/'工程.hstudio')['notes'], notes)
            self.assertFalse(off_report['skip_long_rests'])
            self.assertEqual((off_report['skipped_long_rests'], off_report['removed_rest_seconds']), (0, 0))
            self.assertAlmostEqual(off_report['duration_seconds'] - report['duration_seconds'], 6.8)

    def test_older_projects_without_option_keep_previous_export_behavior(self):
        with tempfile.TemporaryDirectory() as temp:
            notes = [note(0, .1), note(4, 4.1)]
            folder, report = export_project(make_project(notes), temp)
            self.assertFalse(report['skip_long_rests'])
            self.assertEqual(json.loads((folder/'音符.json').read_text('utf-8')), decode_events(build_events(notes)[0]))


class LongRestTransportTests(unittest.TestCase):
    def maps(self, notes, enabled=True):
        performance, _, _ = compress_long_rests(notes, enabled)
        actual = decode_events(build_events(performance)[0])
        anchors = playback_anchors(notes, actual)
        return actual, TimeMap(anchors), TimeMap((b, a) for a, b in anchors)

    def test_long_sounding_note_keeps_normal_cursor_speed_before_shortened_rest(self):
        notes = [note(2, 12), note(20, 21, 62), note(21, 22, 73)]
        actual, forward, inverse = self.maps(notes)
        for score in (2, 3, 7, 11, 12):
            self.assertAlmostEqual(forward(score), score + .1)
            self.assertAlmostEqual(inverse(score + .1), score)
        self.assertAlmostEqual(actual[1]['start'] - actual[0]['end'], .6)
        for fraction in (0, .25, .5, .75, 1):
            self.assertAlmostEqual(inverse(actual[0]['end'] + .6 * fraction), 12 + 8 * fraction)
        # The ordinary 45 ms key-release interval remains smooth, without a flat spot.
        for physical in (actual[1]['end'], actual[1]['end'] + .02, actual[2]['start']):
            self.assertAlmostEqual(inverse(physical), physical + 7.3)

    def test_pause_seek_and_inverse_positions_remain_defined_through_multiple_rests(self):
        notes = [note(.2, .7), note(6, 6.3, 73), note(6.3, 6.31, 60), note(15, 15.2, 64)]
        actual, forward, inverse = self.maps(notes)
        physical = [actual[0]['start'] + (actual[-1]['end']-actual[0]['start']) * i/500 for i in range(501)]
        logical = [inverse(position) for position in physical]
        self.assertTrue(all(a < b for a, b in zip(logical, logical[1:])))
        for audio, score in zip(physical, logical):
            self.assertAlmostEqual(forward(score), audio)
        for source, played in zip(notes, actual):
            self.assertAlmostEqual(forward(source['start']), played['start'])
        self.assertAlmostEqual(forward(notes[-1]['end']), actual[-1]['end'])
        for score in (1, 3, 5.8, 8, 10, 14):
            self.assertAlmostEqual(inverse(forward(score)), score)

    def test_disabled_long_rests_keep_original_onset_mapping(self):
        notes = [note(1, 5), note(10, 11, 62)]
        actual, forward, inverse = self.maps(notes, False)
        self.assertEqual(len(playback_anchors(notes, actual)), len(notes) + 1)
        for score in (0, 1, 3, 5, 8, 10, 11):
            self.assertAlmostEqual(forward(score), score + .1)
            self.assertAlmostEqual(inverse(score + .1), score)


if __name__ == '__main__':
    unittest.main()
