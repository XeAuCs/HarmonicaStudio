"""Interactive editor regressions; run with PySide6 installed and Qt offscreen."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import QPoint, QRectF, Qt
    from PySide6.QtGui import QPainter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from harmonica_studio.editor import NoteEditor
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


def note(p=60, start=.123, end=.8):
    return dict(pitch=p, start=start, end=end, velocity=80)


@unittest.skipUnless(QT_AVAILABLE, 'Qt widget tests require PySide6-Essentials')
class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editor = NoteEditor()
        self.editor.resize(850, 280)
        self.editor.show()
        self.app.processEvents()
        self.editor.set_notes([note(), note(64, 1.2, 1.7)])
        self.app.processEvents()
        self.changed, self.messages, self.seeks = [], [], []
        self.editor.notesChanged.connect(self.changed.append)
        self.editor.message.connect(self.messages.append)
        self.editor.seekRequested.connect(self.seeks.append)

    def tearDown(self):
        self.editor.close()
        self.editor.deleteLater()
        self.app.processEvents()

    def at_note(self, index, fraction=.3):
        rect = self.editor._rect(self.editor.get_notes()[index])
        return QPoint(round(rect.x() + rect.width() * fraction), round(rect.center().y()))

    def at_time(self, seconds, pitch=62):
        return QPoint(round(self.editor.LEFT + seconds * self.editor._zoom - self.editor._view_offset),
                      round(self.editor.RULER + (self.editor.HIGH - pitch + .5) * self.editor.ROW
                            - self.editor.verticalScrollBar().value()))

    def drag(self, start, end):
        viewport = self.editor.viewport()
        QTest.mousePress(viewport, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(viewport, end, 10)
        QTest.mouseRelease(viewport, Qt.LeftButton, Qt.NoModifier, end)
        self.app.processEvents()

    def test_load_and_get_are_independent_copies(self):
        source = [note()]
        self.editor.set_notes(source)
        source[0]['pitch'] = 70
        retrieved = self.editor.get_notes()
        retrieved[0]['pitch'] = 72
        self.assertEqual(self.editor.get_notes()[0]['pitch'], 60)
        self.assertFalse(self.editor.can_undo)
        self.assertFalse(self.changed)

    def test_vertical_drag_preserves_unsnapped_timing(self):
        old = self.editor.get_notes()
        start = self.at_note(0)
        self.drag(start, start + QPoint(0, -40))
        now = self.editor.get_notes()
        self.assertEqual(now[0], dict(old[0], pitch=62))
        self.assertEqual(now[1], old[1])
        self.assertEqual(len(self.changed), 1)

    def test_horizontal_drag_snaps_only_changed_note(self):
        old = self.editor.get_notes()
        start = self.at_note(0)
        self.drag(start, start + QPoint(14, 0))
        now = self.editor.get_notes()
        self.assertAlmostEqual(now[0]['start'], .3)
        self.assertAlmostEqual(now[0]['end'] - now[0]['start'], old[0]['end'] - old[0]['start'])
        self.assertEqual(now[1], old[1])

    def test_edge_drag_changes_duration_without_moving_start(self):
        start = self.at_note(0, .98)
        self.drag(start, start + QPoint(14, 0))
        now = self.editor.get_notes()
        self.assertEqual(now[0]['start'], .123)
        self.assertAlmostEqual(now[0]['end'], 1.0)

    def test_overlap_rejects_and_keeps_original_without_history(self):
        old = self.editor.get_notes()
        start = self.at_note(0)
        self.drag(start, start + QPoint(56, 0))
        self.assertEqual(self.editor.get_notes(), old)
        self.assertFalse(self.editor.can_undo)
        self.assertFalse(self.changed)
        self.assertIn('一次只能', self.messages[-1])

    def test_double_click_adds_then_undo_redo_restore(self):
        original = self.editor.get_notes()
        QTest.mouseDClick(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, self.at_time(2.5))
        self.assertEqual(len(self.editor.get_notes()), 3)
        self.assertAlmostEqual(self.editor.get_notes()[-1]['end'], 2.8)
        QTest.keyClick(self.editor, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(self.editor.get_notes(), original)
        self.assertTrue(self.editor.can_redo)
        QTest.keyClick(self.editor, Qt.Key_Y, Qt.ControlModifier)
        self.assertEqual(len(self.editor.get_notes()), 3)

    def test_delete_selected_and_new_edit_clear_redo(self):
        QTest.mouseClick(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, self.at_note(0))
        QTest.keyClick(self.editor, Qt.Key_Delete)
        self.assertEqual(len(self.editor.get_notes()), 1)
        self.editor.undo()
        QTest.mouseDClick(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, self.at_time(3))
        self.assertFalse(self.editor.can_redo)

    def test_read_only_blocks_all_mutations_but_can_seek(self):
        QTest.mouseDClick(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, self.at_time(2.5))
        original = self.editor.get_notes()
        self.editor.set_read_only(True)
        QTest.keyClick(self.editor, Qt.Key_Z, Qt.ControlModifier)
        QTest.keyClick(self.editor, Qt.Key_Delete)
        QTest.mouseDClick(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, self.at_time(3.5))
        start = self.at_note(0)
        self.drag(start, start + QPoint(14, -20))
        self.editor.undo()
        self.editor.redo()
        self.editor.delete_selected()
        self.assertEqual(self.editor.get_notes(), original)
        self.assertFalse(self.editor.can_undo)
        QTest.mouseClick(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(self.editor.LEFT + 70, 10))
        self.assertEqual(self.seeks, [1.0])

    def test_seek_clamps_to_last_note_end(self):
        QTest.mouseClick(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(self.editor.LEFT + 500, 10))
        self.assertEqual(self.seeks, [1.7])
        self.assertFalse(self.changed)

    def test_zoom_and_playback_follow_keep_notes_unchanged(self):
        original = [note(60, 0, 1), note(64, 40, 41)]
        self.editor.set_notes(original)
        self.editor.zoom_in()
        self.editor.set_position(39, 41)
        self.assertGreater(self.editor.horizontalScrollBar().value(), 0)
        self.editor.zoom_out()
        self.assertEqual(self.editor.get_notes(), original)

    def test_invalid_load_cannot_destroy_existing_document(self):
        original = self.editor.get_notes()
        for bad in ([note(47)], [note(60, 1, 1)], [note(), note(64, .5, 1)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.editor.set_notes(bad)
            self.assertEqual(self.editor.get_notes(), original)

    def test_short_imported_notes_keep_logical_timing(self):
        imported = [note(60, .123, .133), note(62, .133, .145)]
        self.editor.set_notes(imported)
        self.assertEqual(self.editor.get_notes(), imported)
        start = self.at_note(0)
        self.drag(start, start + QPoint(0, -20))
        self.assertEqual(self.editor.get_notes(), [dict(imported[0], pitch=61), imported[1]])

    def test_resizing_short_imported_note_enforces_new_minimum(self):
        self.editor.set_notes([note(60, .123, .133)])
        start = self.at_note(0, .99)
        self.drag(start, start + QPoint(-14, 0))
        changed = self.editor.get_notes()[0]
        self.assertAlmostEqual(changed['end'] - changed['start'], .025)

    def test_read_only_unchanged_does_not_emit_recursive_refresh(self):
        calls = []
        def refresh(*state):
            calls.append(state)
            self.editor.set_read_only(True)
        self.editor.historyChanged.connect(refresh)
        self.editor.set_read_only(True)
        self.editor.set_read_only(True)
        self.assertEqual(calls, [(False, False)])

    def test_tiny_overlap_is_normalized_but_duplicate_onset_rejected(self):
        imported = [note(60, 0, .30000000000000004), note(62, .3, .7)]
        self.editor.set_notes(imported)
        self.assertEqual(self.editor.get_notes()[0]['end'], .3)
        self.assertEqual(imported[0]['end'], .30000000000000004)
        with self.assertRaises(ValueError):
            self.editor.set_notes([note(60, 0, 1e-9), note(62, 0, 1e-8)])

    def test_pitch_zoom_keeps_hit_testing_and_drag_timing_correct(self):
        original = self.editor.get_notes()
        self.editor.pitch_zoom_in()
        self.assertGreater(self.editor.ROW, 20)
        start = self.at_note(0)
        self.assertEqual(self.editor._hit(start), 0)
        self.drag(start, start + QPoint(0, -self.editor.ROW))
        self.assertEqual(self.editor.get_notes(), [dict(original[0], pitch=61), original[1]])
        self.editor.pitch_zoom_out()
        self.assertEqual(self.editor._hit(self.at_note(0)), 0)
        self.assertEqual(self.editor.get_notes()[0]['start'], .123)

    def test_fit_pitches_shows_full_range_without_changing_document(self):
        original = [note(48, .123, .8), note(85, 1.2, 1.7)]
        self.editor.set_notes(original)
        self.editor.fit_pitches()
        self.app.processEvents()
        for index in range(2):
            point = self.at_note(index)
            self.assertGreaterEqual(point.y(), self.editor.RULER)
            self.assertLess(point.y(), self.editor.viewport().height())
            self.assertEqual(self.editor._hit(point), index)
        self.assertEqual(self.editor.get_notes(), original)
        self.assertFalse(self.changed)

    def test_compact_preserves_history_and_disables_mutation(self):
        original = self.editor.get_notes()
        start = self.at_note(0)
        self.drag(start, start + QPoint(0, -20))
        edited = self.editor.get_notes()
        self.editor.pitch_zoom_in()
        previous_row = self.editor.ROW
        self.editor.set_compact(True)
        self.assertEqual(self.editor.LEFT, 12)
        self.assertFalse(self.editor.can_undo)
        QTest.mouseDClick(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, self.at_time(3))
        self.editor.undo()
        self.assertEqual(self.editor.get_notes(), edited)
        self.editor.set_compact(False)
        self.assertEqual(self.editor.ROW, previous_row)
        self.assertTrue(self.editor.can_undo)
        self.editor.undo()
        self.assertEqual(self.editor.get_notes(), original)

    def test_theme_change_preserves_notes_and_validates_colors(self):
        original = self.editor.get_notes()
        self.editor.set_theme(dict(accent='#314b66', bg='#f2f4f7', name='blue', ink='invalid-color'))
        self.assertEqual(self.editor._theme['accent'].name(), '#314b66')
        self.assertEqual(self.editor._theme['ink'].name(), self.editor.DEFAULT_THEME['ink'].lower())
        self.editor.grab()
        self.assertEqual(self.editor.get_notes(), original)
        self.assertFalse(self.changed)

    def assert_playhead_centered(self):
        expected = self.editor.LEFT + (self.editor.viewport().width() - self.editor.LEFT) / 2
        x = self.editor.LEFT + self.editor._position * self.editor._zoom - self.editor._view_offset
        self.assertAlmostEqual(x, expected)
        self.assertAlmostEqual(self.editor._time_at(x), self.editor._position)

    def test_playhead_stays_centered_at_start_middle_and_end(self):
        original = [note(60, 0, 1), note(64, 40, 41)]
        self.editor.set_notes(original)
        for position in (0.0, 20.1234, 41.0):
            self.editor.set_position(position, 41)
            self.assert_playhead_centered()
        self.assertEqual(self.editor.get_notes(), original)

    def test_centered_playhead_survives_zoom_resize_and_compact(self):
        self.editor.set_position(.723, 1.7)
        self.editor.zoom_in()
        self.assert_playhead_centered()
        self.editor.resize(973, 320)
        self.app.processEvents()
        self.assert_playhead_centered()
        self.editor.set_compact(True)
        self.app.processEvents()
        self.assert_playhead_centered()
        self.editor.zoom_out()
        self.assert_playhead_centered()
        self.editor.set_compact(False)
        self.assert_playhead_centered()

    def test_centered_motion_is_subpixel_and_hit_testing_uses_same_offset(self):
        self.editor.set_position(.4, 1.7)
        rect = self.editor._rect(self.editor.get_notes()[0])
        self.assertEqual(self.editor._hit(rect.center()), 0)
        self.editor.set_position(.401, 1.7)
        moved = self.editor._rect(self.editor.get_notes()[0])
        self.assertAlmostEqual(rect.x() - moved.x(), .001 * self.editor._zoom)
        self.assert_playhead_centered()
        self.assertAlmostEqual(self.editor._time_at(moved.x()), .123)

    def test_edit_from_centered_start_keeps_anchor_and_original_timing(self):
        original = self.editor.get_notes()
        self.editor.set_position(0, 1.7)
        before = self.editor._view_offset
        start = self.at_note(0)
        QTest.mousePress(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, start)
        self.assertEqual(self.editor._view_offset, before)
        self.assertEqual(self.editor._hit(start), 0)
        self.assertIsNone(self.editor._position)
        end = start + QPoint(0, -20)
        QTest.mouseMove(self.editor.viewport(), end, 10)
        QTest.mouseRelease(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, end)
        self.assertEqual(self.editor.get_notes(), [dict(original[0], pitch=61), original[1]])

    def test_stop_resets_offset_then_edit_and_seek_are_correct(self):
        self.editor.set_position(1.5, 1.7)
        self.editor.set_position(None)
        self.assertEqual(self.editor._view_offset, 0)
        self.assertEqual(self.editor.horizontalScrollBar().value(), 0)
        start = self.at_note(0)
        self.assertEqual(self.editor._hit(start), 0)
        self.drag(start, start + QPoint(0, -20))
        self.assertEqual(self.editor.get_notes()[0]['pitch'], 61)
        QTest.mouseClick(self.editor.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(self.editor.LEFT + 70, 10))
        self.assertEqual(self.seeks, [1])

    def test_manual_scroll_while_centered_seeks_without_breaking_coordinates(self):
        self.editor.set_notes([note(60, 0, 1), note(64, 40, 41)])
        self.editor.set_position(1, 41)
        target = round(10 * self.editor._zoom - self.editor._timeline_width() / 2)
        self.editor.horizontalScrollBar().setValue(target)
        self.assertTrue(self.seeks)
        self.assertAlmostEqual(self.seeks[-1], self.editor._position)
        self.assert_playhead_centered()

    def test_piano_white_keys_are_continuous_and_black_keys_follow_two_three_pattern(self):
        white, black = self.editor._piano_geometry()
        octave_naturals = [60, 62, 64, 65, 67, 69, 71, 72]
        for lower, higher in zip(octave_naturals, octave_naturals[1:]):
            self.assertAlmostEqual(white[lower].top(), white[higher].bottom())
        self.assertEqual([pitch for pitch in black if 60 <= pitch < 72], [61, 63, 66, 68, 70])
        for pitch in (61, 63, 66, 68, 70):
            self.assertAlmostEqual(black[pitch].center().y(), self.editor._pitch_center(pitch))
            self.assertAlmostEqual(white[pitch - 1].top(), black[pitch].center().y())
            self.assertLess(black[pitch].width(), white[pitch - 1].width())
        for lower, higher in ((64, 65), (71, 72)):
            join = white[lower].top()
            self.assertAlmostEqual(join, (self.editor._pitch_center(lower) + self.editor._pitch_center(higher)) / 2)
            self.assertFalse(any(rect.top() < join < rect.bottom() for rect in black.values()))

    def test_active_key_tracks_notes_and_is_cleared_on_stop(self):
        self.editor.set_position(.2, 1.7)
        self.assertEqual(self.editor._active_pitch(), 60)
        self.editor.set_position(.9, 1.7)
        self.assertIsNone(self.editor._active_pitch())
        self.editor.set_position(1.3, 1.7)
        self.assertEqual(self.editor._active_pitch(), 64)
        self.editor.set_position(None)
        self.assertIsNone(self.editor._active_pitch())

    def test_white_key_text_stays_centered_inside_keys_at_different_pitch_zooms(self):
        captured = {}

        class RecordingPainter(QPainter):
            def drawText(self, *args):
                if len(args) == 3 and isinstance(args[0], QRectF):
                    captured[args[2]] = (QRectF(args[0]), args[1])
                return super().drawText(*args)

        self.editor.resize(850, 500)
        for row in (9, 10, 12, 20, 32):
            with self.subTest(row=row):
                self.editor.ROW = row
                self.editor._update_ranges()
                self.editor.verticalScrollBar().setValue((self.editor.HIGH - 79) * row)
                self.app.processEvents()
                captured.clear()
                with patch('harmonica_studio.editor.QPainter', RecordingPainter):
                    self.editor.viewport().grab()
                white, _ = self.editor._piano_geometry()
                for pitch in (71, 72, 76, 77):
                    rect, alignment = captured[self.editor._pitch_label(pitch)]
                    self.assertTrue(white[pitch].contains(rect))
                    self.assertAlmostEqual(rect.center().y(), white[pitch].center().y())
                    self.assertTrue(alignment & Qt.AlignVCenter)
                    self.assertTrue(alignment & Qt.AlignHCenter)
                for lower, higher in ((71, 72), (76, 77)):
                    self.assertFalse(captured[self.editor._pitch_label(lower)][0].intersects(
                        captured[self.editor._pitch_label(higher)][0]))

    def test_explicit_timeline_reset_returns_manually_browsed_score_to_start(self):
        original = self.editor.get_notes()
        self.editor.pitch_zoom_in()
        vertical = self.editor.verticalScrollBar().value()
        row = self.editor.ROW
        self.editor.horizontalScrollBar().setValue(50)
        self.editor.reset_timeline()
        self.assertEqual(self.editor._view_offset, 0)
        self.assertEqual(self.editor.horizontalScrollBar().value(), 0)
        self.assertEqual(self.editor.verticalScrollBar().value(), vertical)
        self.assertEqual(self.editor.ROW, row)
        self.assertEqual(self.editor.get_notes(), original)


if __name__ == '__main__':
    unittest.main()
