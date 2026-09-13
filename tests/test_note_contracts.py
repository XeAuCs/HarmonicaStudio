"""Shared editor/project contracts to protect the P2 validation extraction."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest

from harmonica_studio.melody import prepare
from harmonica_studio.midi import read_midi, write_midi
from harmonica_studio.models import Options
from harmonica_studio.project import make_project, validate_project

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    import PySide6
except ModuleNotFoundError:
    QT_AVAILABLE = False
else:
    from PySide6.QtWidgets import QApplication
    from harmonica_studio.editor import NoteEditor
    QT_AVAILABLE = True


def note(pitch=60, start=0, end=.2, velocity=80):
    return dict(pitch=pitch, start=start, end=end, velocity=velocity)


class MidiBoundaryContractTests(unittest.TestCase):
    def test_midi_outside_harmonica_range_survives_until_octave_adaptation(self):
        for pitch in (36, 96):
            with self.subTest(pitch=pitch), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'wide-range.mid'
                write_midi([note(pitch)], path)
                parts, names = read_midi(path)
                self.assertEqual(parts[(0, 0)][0]['pitch'], pitch)
                before = deepcopy(parts)
                prepared, report = prepare(parts, names, Options())
                self.assertEqual(parts, before)
                self.assertEqual(len(prepared), 1)
                self.assertTrue(48 <= prepared[0]['pitch'] <= 85)
                self.assertEqual(prepared[0]['pitch'], pitch + report['transpose_semitones'])
                self.assertEqual(make_project(prepared)['notes'], prepared)


class ProjectDefaultContractTests(unittest.TestCase):
    def test_project_rejects_boolean_times_and_supplies_default_velocity(self):
        for fields in ({'start': False}, {'end': True}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                make_project([dict(note(), **fields)])
        self.assertEqual(make_project([dict(pitch=60, start=0, end=.2)])['notes'], [note()])


@unittest.skipUnless(QT_AVAILABLE, 'Cross-entry validation requires PySide6-Essentials')
class NoteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = NoteEditor()
        self.addCleanup(self.editor.deleteLater)

    def test_valid_score_corpus_normalizes_identically_without_mutating_input(self):
        cases = {
            'empty': [],
            'range_and_velocity_edges': [note(48, 0, .1, 1), note(85, .1, .2, 127)],
            'short_imported_notes': [note(60, .123, .133), note(62, .133, .145)],
            'duration_limit': [note(60, 1199.9, 1200)],
            'unsorted': [note(62, .3, .4), note()],
            'rounding_join': [note(60, 1.4, 1.7000000000000002), note(62, 1.7, 2.1)],
        }
        for label, notes in cases.items():
            with self.subTest(case=label):
                original = deepcopy(notes)
                project = make_project(notes)
                self.editor.set_notes(notes)
                self.assertEqual(self.editor.get_notes(), project['notes'])
                self.assertEqual(notes, original)
                self.editor.set_notes(self.editor.get_notes())
                self.assertEqual(self.editor.get_notes(), project['notes'])
                self.assertEqual(validate_project(project), project)

    def test_invalid_score_corpus_is_rejected_by_both_entry_points(self):
        cases = {
            'pitch_low': [note(47)], 'pitch_high': [note(86)],
            'boolean_pitch': [note(True)], 'fractional_pitch': [note(60.5)],
            'velocity_low': [note(velocity=0)], 'velocity_high': [note(velocity=128)],
            'boolean_velocity': [note(velocity=True)],
            'negative_start': [note(start=-.1)], 'empty_duration': [note(end=0)],
            'duration_limit': [note(end=1200.01)], 'text_time': [note(start='0')],
            'nan_time': [note(start=float('nan'))], 'infinite_time': [note(end=float('inf'))],
            'overlap': [note(), note(62, .1, .3)],
            'same_start': [note(), note(62)],
            'real_overlap_near_join': [note(end=.2001), note(62, .2, .4)],
        }
        initial = [note(64)]
        self.editor.set_notes(initial)
        for label, notes in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    make_project(notes)
                with self.assertRaises(ValueError):
                    self.editor.set_notes(notes)
                self.assertEqual(self.editor.get_notes(), initial)

    def test_editor_rejects_boolean_time_like_project_validation(self):
        notes = [note(start=False)]
        with self.assertRaises(ValueError):
            self.editor.set_notes(notes)

    def test_missing_velocity_has_same_default_at_both_entry_points(self):
        notes = [dict(pitch=60, start=0, end=.2)]
        self.editor.set_notes(notes)
        self.assertEqual(self.editor.get_notes(), make_project(notes)['notes'])


if __name__ == '__main__':
    unittest.main()
