"""Small labelled musical counterexamples, not a real-world accuracy benchmark."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from harmonica_studio.melody import simplify, rank_parts, prepare, part_features
from harmonica_studio.models import Options
from harmonica_studio.project import make_project, save_project, load_project
from harmonica_studio.storage import save_options, load_options


def note(pitch, start, end, velocity=80):
    return dict(pitch=pitch, start=start, end=end, velocity=velocity)


class MelodyQualityTests(unittest.TestCase):
    def test_staggered_polyphony_is_not_monophonic(self):
        overlapping = [note(60, i*.1, i*.1+1) for i in range(10)]
        mono = [note(60, i*.1, i*.1+.05) for i in range(10)]
        self.assertLess(part_features(overlapping, 0, 2)['monophony'], .2)
        self.assertEqual(part_features(mono, 0, 2)['monophony'], 1)
        self.assertEqual(rank_parts({(0, 0): overlapping, (1, 0): mono}, {})[0][0], (1, 0))

    def test_misleading_track_name_does_not_override_clear_melody(self):
        melody = [note(p, i*.5, i*.5+.45) for i, p in enumerate([72, 74, 76, 74]*4)]
        accompaniment = [note(p, i*.5, i*.5+.45) for i in range(16) for p in (48, 52, 55)]
        parts = {(0, 0): accompaniment, (1, 0): melody}
        self.assertEqual(rank_parts(parts, {0: 'Melody'})[0][0], (1, 0))
        self.assertEqual(rank_parts({(0, 0): [], **{(1, 0): melody}}, {})[0][0], (1, 0))
        self.assertEqual(rank_parts({}, {}), [])

    def test_consecutive_fast_notes_and_real_leaps_survive_all_modes(self):
        source = [note(60, 0, .02), note(84, .02, .04), note(48, .04, .06)]
        for mode in ('sustain', 'highest', 'continuous'):
            with self.subTest(mode=mode):
                self.assertEqual(simplify(source, mode, trim=False), source)

    def test_continuous_preserves_long_note_over_nearby_lower_accompaniment(self):
        melody = [note(72, 0, 2), note(74, 2, 2.5)]
        source = melody + [note(67, .5, .8), note(65, 1, 1.3)]
        self.assertEqual(simplify(source, 'continuous'), melody)

    def test_continuous_follows_middle_voice_past_short_high_ornaments(self):
        melody = [note(p, i*.5, i*.5+.5) for i, p in enumerate([72, 74, 76, 74])]
        source = melody + [note(91, i*.5+.01, i*.5+.06) for i in range(4)]
        self.assertEqual(simplify(source, 'continuous'), melody)

    def test_continuous_does_not_fill_rests_or_change_source(self):
        source = [note(60, 2, 2.4), note(79, 4, 4.5)]
        before = deepcopy(source)
        self.assertEqual(simplify(source, 'continuous', trim=False), before)
        self.assertEqual(simplify(source, 'continuous')[1]['start'], 2)
        self.assertEqual(source, before)

    def test_legato_overlap_is_trimmed_without_dropping_next_melody_note(self):
        source = [note(72, 0, .52), note(74, .5, 1.02), note(76, 1, 1.5)]
        result = simplify(source, 'continuous')
        self.assertEqual([n['pitch'] for n in result], [72, 74, 76])
        self.assertEqual([n['end'] for n in result], [.5, 1, 1.5])

    def test_phrase_octaves_keep_wide_separated_phrases_and_timing(self):
        source = [note(36, 2, 2.5), note(40, 2.5, 3), note(96, 4, 4.5), note(100, 4.5, 5)]
        before = deepcopy(source)
        options = Options(melody_mode='continuous', phrase_octave=True, trim_silence=False, speed=.5)
        result, report = prepare({(0, 0): source}, {}, options)
        self.assertEqual(len(result), 4)
        self.assertTrue(all(48 <= n['pitch'] <= 85 for n in result))
        self.assertEqual([result[1]['pitch']-result[0]['pitch'], result[3]['pitch']-result[2]['pitch']], [4, 4])
        self.assertEqual([(n['start'], n['end']) for n in result], [(4, 5), (5, 6), (8, 9), (9, 10)])
        self.assertEqual(report['dropped_out_of_range'], 0)
        self.assertGreater(report['phrase_adjusted_notes'], 0)
        self.assertTrue(all(a['start'] >= 4 for a in report['octave_adjustments']))
        self.assertEqual(source, before)
        default, old_report = prepare({(0, 0): source}, {}, Options())
        self.assertEqual(len(default), 2)
        self.assertEqual(old_report['phrase_adjusted_notes'], 0)

    def test_phrase_mode_does_not_fold_individual_notes_in_unbroken_phrase(self):
        source = [note(36, 0, .5), note(96, .5, 1)]
        result, report = prepare({(0, 0): source}, {}, Options(phrase_octave=True))
        self.assertEqual(len(result), 1)
        self.assertEqual(report['dropped_out_of_range'], 1)

    def test_phrase_mode_leaves_in_range_score_unchanged(self):
        source = [note(60, 0, .5), note(72, 2, 2.5)]
        result, report = prepare({(0, 0): source}, {}, Options(phrase_octave=True))
        self.assertEqual(result, source)
        self.assertEqual(report['octave_adjustments'], [])

    def test_global_fit_protects_long_notes_and_handles_extreme_manual_transpose(self):
        source = [note(36, 0, .04), note(36, .04, .08), note(96, 1, 4)]
        result, _ = prepare({(0, 0): source}, {}, Options())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['end'] - result[0]['start'], 3)
        result, _ = prepare({(0, 0): [note(0, 0, 1)]}, {}, Options(transpose=-24))
        self.assertEqual(result[0]['pitch'], 48)

    def test_options_and_adjustment_provenance_round_trip(self):
        options = Options(melody_mode='continuous', phrase_octave=True)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            save_options(path/'settings.json', options)
            self.assertEqual(load_options(path/'settings.json'), options)
            source = [note(36, 0, .5), note(96, 2, 2.5)]
            notes, report = prepare({(0, 0): source}, {}, options)
            project = make_project(notes, options=asdict(options), report=report)
            save_project(path/'score.hstudio', project)
            self.assertEqual(load_project(path/'score.hstudio'), project)
        with self.assertRaises(ValueError):
            Options(phrase_octave='yes').validate()

    def test_reexport_preserves_adapted_notes_without_reapplying_octaves(self):
        from harmonica_studio.midi import write_midi
        from harmonica_studio.service import convert, export_project
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'wide.mid'
            write_midi([note(36, 0, .5), note(96, 2, 2.5)], source)
            folder, _ = convert(source, root/'exports', Options(melody_mode='continuous', phrase_octave=True))
            project = load_project(folder/'工程.hstudio')
            canonical = deepcopy(project['notes'])
            provenance = deepcopy(project['report']['octave_adjustments'])
            for _ in range(2):
                folder, report = export_project(project, root/'exports')
                project = load_project(folder/'工程.hstudio')
                self.assertEqual(project['notes'], canonical)
                self.assertEqual(report['octave_adjustments'], provenance)


if __name__ == '__main__':
    unittest.main()
