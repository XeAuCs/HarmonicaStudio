"""A small, monophonic piano roll. Timeline positions are seconds, never beats."""
from copy import deepcopy
from bisect import bisect_left, bisect_right
import math

from PySide6.QtCore import QEvent, QLineF, QPointF, QRectF, Qt, Signal, QSize
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QToolTip


class NoteEditor(QAbstractScrollArea):
    notesChanged = Signal(list)
    seekRequested = Signal(float)
    message = Signal(str)
    historyChanged = Signal(bool, bool)

    LEFT = 136
    RULER = 28
    ROW = 20
    LOW, HIGH = 48, 85
    MIN_LENGTH = .025
    MAX_TIME = 1200.0
    SNAP = .05
    DEFAULT_THEME = dict(bg='#F4F1EA', surface='#FCFAF5', ink='#292720',
                         muted='#6E695F', line='#CFC8BB', grid='#E5DED2',
                         accent='#9F4937', accent_soft='#EFE0D8', accent_ink='#FFF8F0',
                         selection='#E6D8CC', playhead='#B15339')

    def __init__(self, parent=None):
        super().__init__(parent)
        self._notes = []
        self._undo, self._redo = [], []
        self._selected = None
        self._drag = None
        self._read_only = False
        self._compact = False
        self._fit_pitch_mode = False
        self._pitch_view_before_compact = None
        self._theme = {key: QColor(value) for key, value in self.DEFAULT_THEME.items()}
        self._zoom = 70.0
        self._position = None
        self._view_offset = 0.0
        self._syncing_scroll = False
        self._duration = 0.0
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumHeight(245)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('可编辑口琴曲谱')
        self.setToolTip('方块内的字母表示演奏按键。悬停查看音高、按键和时间；拖动音符编辑，点击上方时间尺试听。')
        self.viewport().setMouseTracking(True)
        self.horizontalScrollBar().valueChanged.connect(self._manual_scroll)
        self.verticalScrollBar().valueChanged.connect(self.viewport().update)
        self._update_ranges()

    def sizeHint(self):
        return QSize(850, 280)

    @property
    def can_undo(self):
        return bool(self._undo) and self._editable

    @property
    def can_redo(self):
        return bool(self._redo) and self._editable

    @property
    def _editable(self):
        return not self._read_only and not self._compact

    def set_theme(self, palette):
        """Accept color values from theme_palette(); unknown metadata is ignored."""
        for key in self.DEFAULT_THEME:
            if key in palette:
                color = QColor(palette[key])
                if color.isValid():
                    self._theme[key] = color
        self.viewport().update()

    def set_palette(self, palette):
        self.set_theme(palette)

    def set_compact(self, compact):
        """Show a quiet, read-only roll without changing the document or history."""
        compact = bool(compact)
        if compact == self._compact:
            return
        self._drag = self._selected = None
        self._compact = compact
        if compact:
            self._pitch_view_before_compact = (self.ROW, self.verticalScrollBar().value(), self._fit_pitch_mode)
            self.LEFT = 12
            self.fit_pitches()
        else:
            self.LEFT = type(self).LEFT
            if self._pitch_view_before_compact:
                self.ROW, scroll, self._fit_pitch_mode = self._pitch_view_before_compact
                self._update_ranges()
                self.verticalScrollBar().setValue(scroll)
                if self._fit_pitch_mode:
                    self.fit_pitches()
        self._update_ranges()
        self.viewport().unsetCursor()
        self._history_changed()
        self.viewport().update()

    def pitch_zoom_in(self):
        self._set_pitch_zoom(self.ROW * 1.25)

    def pitch_zoom_out(self):
        self._set_pitch_zoom(self.ROW / 1.25)

    def fit_pitches(self):
        """Fit the current note range with a little space above and below."""
        self._fit_pitch_mode = True
        pitches = [note['pitch'] for note in self._notes]
        lo, hi = (min(pitches), max(pitches)) if pitches else (60, 71)
        count = min(self.HIGH, hi + 1) - max(self.LOW, lo - 1) + 1
        available = max(1, self.viewport().height() - self.RULER - 2)
        self.ROW = max(5, min(44, available // count))
        self._drag = None
        self._update_ranges()
        self._center_pitches()
        self.viewport().update()

    def _set_pitch_zoom(self, row_height):
        height = max(1, self.viewport().height() - self.RULER)
        center = (self.verticalScrollBar().value() + height / 2) / self.ROW
        self._fit_pitch_mode = False
        self._drag = None
        self.ROW = max(5, min(44, round(row_height)))
        self._update_ranges()
        self.verticalScrollBar().setValue(round(center * self.ROW - height / 2))
        self.viewport().update()

    def get_notes(self):
        return deepcopy(self._notes)

    def set_notes(self, notes):
        """Load a document without emitting notesChanged or creating undo history."""
        notes = sorted(deepcopy(notes), key=lambda n: (n['start'], n['pitch']))
        self._validate(notes)
        self._notes = notes
        self._undo.clear()
        self._redo.clear()
        self._selected = self._drag = self._position = None
        self._view_offset = 0.0
        self._duration = 0.0
        self._update_ranges()
        self._set_view_offset(0.0)
        self.fit_pitches() if self._fit_pitch_mode else self._center_pitches()
        self._history_changed()
        self.viewport().update()

    def set_read_only(self, read_only):
        read_only = bool(read_only)
        if read_only == self._read_only:
            return
        self._read_only = read_only
        self._drag = None
        self.viewport().unsetCursor()
        self._history_changed()
        self.viewport().update()

    def set_position(self, seconds, duration=0):
        was_following = self._position is not None
        self._position = None if seconds is None else max(0.0, min(self.MAX_TIME, float(seconds)))
        if duration and duration != self._duration:
            self._duration = max(0.0, min(self.MAX_TIME, float(duration)))
            self._update_ranges()
        if self._position is not None:
            self._set_view_offset(self._position * self._zoom - self._timeline_width() / 2)
        elif was_following:
            # Explicit stop returns to the score start. Starting an edit clears
            # _position directly, so that operation retains its visual anchor.
            self._set_view_offset(0.0)
        self.viewport().update()

    def _timeline_width(self):
        return max(1, self.viewport().width() - self.LEFT)

    def reset_timeline(self):
        """Explicit transport stop; keep pitch zoom, selection and score intact."""
        self._position = None
        self._set_view_offset(0.0)

    def _set_view_offset(self, value):
        """Keep subpixel motion independent of the integer scrollbar thumb."""
        self._view_offset = float(value)
        self._syncing_scroll = True
        try:
            self.horizontalScrollBar().setValue(round(value))
        finally:
            self._syncing_scroll = False
        self.viewport().update()

    def _manual_scroll(self, value):
        if self._syncing_scroll:
            return
        self._view_offset = float(value)
        if self._position is not None:
            end = self._duration or max((n['end'] for n in self._notes), default=0)
            position = max(0.0, min(end, (value + self._timeline_width() / 2) / self._zoom))
            self.set_position(position)
            self.seekRequested.emit(position)
        self.viewport().update()

    def undo(self):
        if not self.can_undo:
            return
        self._redo.append(deepcopy(self._notes))
        self._notes = self._undo.pop()
        self._after_edit()
        self.message.emit('已撤销上一步修改。')

    def redo(self):
        if not self.can_redo:
            return
        self._undo.append(deepcopy(self._notes))
        self._notes = self._redo.pop()
        self._after_edit()
        self.message.emit('已重做修改。')

    def delete_selected(self):
        if not self._editable or self._selected is None:
            return
        notes = self.get_notes()
        del notes[self._selected]
        if self._commit(notes):
            self.message.emit('已删除音符。可用 Ctrl+Z 撤销。')

    def zoom_in(self):
        self._set_zoom(self._zoom * 1.25)

    def zoom_out(self):
        self._set_zoom(self._zoom / 1.25)

    def _set_zoom(self, zoom, anchor_x=None):
        x = self.viewport().width() / 2 if anchor_x is None else anchor_x
        x = max(self.LEFT, x)
        seconds = self._time_at(x)
        self._zoom = min(350.0, max(20.0, zoom))
        self._update_ranges()
        if self._position is None:
            bar = self.horizontalScrollBar()
            self._set_view_offset(max(bar.minimum(), min(bar.maximum(), seconds * self._zoom - (x - self.LEFT))))
        self.viewport().update()

    def _validate(self, notes):
        previous = None
        for note in notes:
            start, end, pitch = note['start'], note['end'], note['pitch']
            if (isinstance(pitch, bool) or not isinstance(pitch, int)
                    or not self.LOW <= pitch <= self.HIGH):
                raise ValueError('音高需要在 C3 至 C♯6 之间。')
            if (not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                    or not math.isfinite(start) or not math.isfinite(end)
                    or start < 0 or end > self.MAX_TIME or end <= start):
                raise ValueError('音符时长必须大于零，曲谱不能超过 20 分钟。')
            velocity = note.get('velocity', 80)
            if isinstance(velocity, bool) or not isinstance(velocity, int) or not 1 <= velocity <= 127:
                raise ValueError('音符力度需要在 1 至 127 之间。')
            if previous is not None and start < previous['end']:
                if previous['end'] - start <= 1e-8 and start > previous['start']:
                    # Match project validation for harmless floating-point joins.
                    previous['end'] = start
                else:
                    raise ValueError('这里已有音符。口琴一次只能演奏一个音，请移到空白时间。')
            previous = note

    def _commit(self, notes, selected=None):
        notes = sorted(notes, key=lambda n: (n['start'], n['pitch']))
        try:
            self._validate(notes)
        except ValueError as exc:
            self.message.emit(str(exc))
            self.viewport().update()
            return False
        if notes == self._notes:
            return False
        self._undo.append(deepcopy(self._notes))
        self._undo = self._undo[-100:]
        self._redo.clear()
        self._notes = deepcopy(notes)
        self._after_edit()
        if selected is not None:
            self._selected = next((i for i, n in enumerate(self._notes) if n == selected), None)
        return True

    def _after_edit(self):
        self._selected = self._drag = self._position = None
        self._duration = 0.0
        self._update_ranges()
        self._history_changed()
        self.notesChanged.emit(self.get_notes())
        self.viewport().update()

    def _history_changed(self):
        self.historyChanged.emit(self.can_undo, self.can_redo)

    def _update_ranges(self):
        width = self._timeline_width()
        height = max(1, self.viewport().height() - self.RULER)
        end = max((n['end'] for n in self._notes), default=0)
        total = min(self.MAX_TIME, max(8.0, end + 3, self._duration))
        h = self.horizontalScrollBar()
        self._syncing_scroll = True
        try:
            h.setPageStep(width)
            h.setSingleStep(round(self._zoom))
            # Half a viewport of padding makes both 0 seconds and the final
            # note reachable at the center, including short scores.
            h.setRange(-math.ceil(width / 2), math.ceil(total * self._zoom - width / 2))
        finally:
            self._syncing_scroll = False
        v = self.verticalScrollBar()
        v.setPageStep(height)
        v.setSingleStep(self.ROW)
        v.setRange(0, max(0, (self.HIGH - self.LOW + 1) * self.ROW - height))
        if self._position is not None:
            self._set_view_offset(self._position * self._zoom - width / 2)
        else:
            self._set_view_offset(max(h.minimum(), min(h.maximum(), self._view_offset)))

    def _center_pitches(self):
        pitch = ((min(n['pitch'] for n in self._notes) + max(n['pitch'] for n in self._notes)) / 2
                 if self._notes else 64)
        height = self.viewport().height() - self.RULER
        self.verticalScrollBar().setValue(round((self.HIGH - pitch + .5) * self.ROW - height / 2))

    def _time_at(self, x):
        return max(0.0, min(self.MAX_TIME,
                           (x - self.LEFT + self._view_offset) / self._zoom))

    def _pitch_at(self, y):
        row = math.floor((y - self.RULER + self.verticalScrollBar().value()) / self.ROW)
        return max(self.LOW, min(self.HIGH, self.HIGH - row))

    def _rect(self, note):
        note_height = min(6, self.ROW - 2) if self._compact else max(2, self.ROW - min(6, self.ROW * .3))
        inset = (self.ROW - note_height) / 2
        return QRectF(self.LEFT + note['start'] * self._zoom - self._view_offset,
                      self.RULER + (self.HIGH - note['pitch']) * self.ROW
                      - self.verticalScrollBar().value() + inset,
                      max(4.0, (note['end'] - note['start']) * self._zoom), note_height)

    def _hit(self, pos):
        if pos.x() < self.LEFT or pos.y() < self.RULER:
            return None
        for index in self._visible_indices():
            note = self._notes[index]
            if self._rect(note).adjusted(-1, -2, 1, 2).contains(pos):
                return index
        return None

    def _visible_indices(self):
        # Monophonic notes have ordered starts and ends: avoid walking a whole
        # long MIDI every playback tick or pointer movement.
        left = self._view_offset / self._zoom - 5 / self._zoom
        right = left + (self.viewport().width() - self.LEFT + 10) / self._zoom
        begin = bisect_left(self._notes, left, key=lambda n: n['end'])
        end = bisect_right(self._notes, right, key=lambda n: n['start'])
        result = list(range(begin, end))
        if self._drag and self._drag['index'] not in result:
            result.append(self._drag['index'])
        return result

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_ranges()
        if self._fit_pitch_mode:
            self.fit_pitches()

    def scrollContentsBy(self, dx, dy):
        # Labels and ruler remain frozen while only note coordinates scroll.
        self.viewport().update()

    def _pitch_center(self, pitch):
        return self.RULER + (self.HIGH - pitch + .5) * self.ROW - self.verticalScrollBar().value()

    def _piano_geometry(self):
        """Contiguous white keys with accidentals laid across their boundaries.

        Natural-key boundaries are halfway between natural pitches. A missing
        accidental at E/F or B/C therefore has a plain white-key join; the other
        five boundaries carry a black key centered on its exact semitone row.
        """
        naturals = [p for p in range(self.LOW - 4, self.HIGH + 5) if p % 12 not in (1, 3, 6, 8, 10)]
        white = {}
        for index, pitch in enumerate(naturals[1:-1], start=1):
            center = self._pitch_center(pitch)
            top = (center + self._pitch_center(naturals[index + 1])) / 2
            bottom = (center + self._pitch_center(naturals[index - 1])) / 2
            white[pitch] = QRectF(0, top, 96, bottom - top)
        black = {pitch: QRectF(0, self._pitch_center(pitch) - self.ROW * .42, 62, self.ROW * .84)
                 for pitch in range(self.LOW, self.HIGH + 1) if pitch % 12 in (1, 3, 6, 8, 10)}
        return white, black

    def _active_pitch(self):
        if self._position is None:
            return None
        index = bisect_right(self._notes, self._position, key=lambda note: note['start']) - 1
        if index >= 0 and self._notes[index]['start'] <= self._position < self._notes[index]['end']:
            return self._notes[index]['pitch']
        return None

    def _paint_piano(self, painter, height):
        colors = self._theme
        font = painter.font()
        white, black = self._piano_geometry()
        active = self._active_pitch()
        painter.save()
        painter.setClipRect(QRectF(0, self.RULER, self.LEFT, height - self.RULER))
        painter.fillRect(QRectF(0, self.RULER, self.LEFT, height), colors['surface'])
        keyboard_top = max(self.RULER, self._pitch_center(self.HIGH) - self.ROW / 2)
        keyboard_bottom = min(height, self._pitch_center(self.LOW) + self.ROW / 2)
        painter.setClipRect(QRectF(0, keyboard_top, self.LEFT, max(0, keyboard_bottom - keyboard_top)), Qt.IntersectClip)

        # Full-depth white keys are painted first. Their continuous fronts stay
        # visible beside the shorter black keys, as on a physical piano.
        for pitch, rect in white.items():
            if rect.bottom() < self.RULER or rect.top() > height:
                continue
            white_fill = QLinearGradient(rect.left(), 0, rect.right(), 0)
            white_fill.setColorAt(0, colors['accent_soft'] if pitch == active else colors['surface'])
            white_fill.setColorAt(.82, colors['accent_soft'] if pitch == active else colors['surface'])
            white_fill.setColorAt(1, colors['selection'] if pitch == active else colors['bg'])
            painter.setPen(QPen(colors['line'], .8))
            painter.setBrush(white_fill)
            painter.drawRect(rect)
            painter.setPen(QPen(colors['surface'], .8))
            painter.drawLine(QLineF(rect.left() + 1, rect.top() + 1, rect.right() - 1, rect.top() + 1))
            center = self._pitch_center(pitch)
            octave = pitch % 12 == 0
            if self.LOW <= pitch <= self.HIGH and (self.ROW >= 9 or octave):
                name_font = painter.font()
                name_font.setPixelSize(12 if self.ROW >= 18 else 10)
                name_font.setBold(octave or pitch == active)
                painter.setFont(name_font)
                painter.setPen(colors['accent'] if octave or pitch == active else colors['ink'])
                painter.drawText(QRectF(66, center - 8, 28, 16), Qt.AlignVCenter | Qt.AlignHCenter, self._pitch_label(pitch))
                if octave:
                    painter.fillRect(QRectF(91, center - min(7, self.ROW * .34), 3, min(14, self.ROW * .68)), colors['accent'])
                painter.setFont(font)

        for pitch, rect in black.items():
            if rect.bottom() < self.RULER or rect.top() > height:
                continue
            shadow = QColor(colors['ink']);shadow.setAlpha(60)
            painter.setPen(Qt.NoPen);painter.setBrush(shadow)
            painter.drawRoundedRect(rect.translated(2, 1), 1, 1)
            black_fill = QLinearGradient(rect.left(), 0, rect.right(), 0)
            base = colors['accent'] if pitch == active else colors['ink']
            black_fill.setColorAt(0, base.lighter(122))
            black_fill.setColorAt(.82, base)
            black_fill.setColorAt(1, base.darker(140))
            painter.setPen(QPen(base.darker(140), .8));painter.setBrush(black_fill)
            painter.drawRoundedRect(rect, 1, 1)
            highlight = QColor(colors['surface']);highlight.setAlpha(74)
            painter.setPen(QPen(highlight, .75))
            painter.drawLine(QLineF(rect.left() + 3, rect.top() + 1.5, rect.right() - 5, rect.top() + 1.5))
            painter.setPen(QPen(base.lighter(145), .7))
            painter.drawLine(QLineF(rect.right() - 4, rect.top() + 2, rect.right() - 4, rect.bottom() - 2))
            if self.ROW >= 13:
                name_font = painter.font();name_font.setPixelSize(11 if self.ROW >= 18 else 9)
                painter.setFont(name_font);painter.setPen(colors['surface'])
                painter.drawText(rect.adjusted(7, 0, -6, 0), Qt.AlignVCenter, self._pitch_label(pitch))
                painter.setFont(font)

        # Key commands have their own column; they no longer break white keys
        # into one rectangular strip per semitone.
        painter.fillRect(QRectF(98, self.RULER, self.LEFT - 98, height), colors['bg'])
        painter.setPen(QPen(colors['line'], .8));painter.drawLine(QLineF(98, self.RULER, 98, height))
        for pitch in range(self.LOW, self.HIGH + 1):
            center = self._pitch_center(pitch)
            if self.RULER <= center <= height and (self.ROW >= 16 or pitch % 12 == 0):
                painter.setPen(colors['accent'] if pitch == active else colors['muted'])
                painter.drawText(QRectF(103, center - 8, self.LEFT - 105, 16), Qt.AlignVCenter, self._key_label(pitch))
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing)
        area = self.viewport().rect()
        colors = self._theme
        painter.fillRect(area, colors['surface'])
        h = self._view_offset
        v = self.verticalScrollBar().value()
        width, height = area.width(), area.height()
        font = painter.font()
        font.setPixelSize(11)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        painter.save()
        painter.setClipRect(QRectF(self.LEFT, self.RULER, width - self.LEFT, height - self.RULER))
        for pitch in range(self.LOW, self.HIGH + 1):
            y = self.RULER + (self.HIGH - pitch) * self.ROW - v
            if y + self.ROW <= self.RULER or y >= height:
                continue
            sharp = pitch % 12 in (1, 3, 6, 8, 10)
            if sharp and not self._compact:
                painter.fillRect(QRectF(self.LEFT, y, width - self.LEFT, self.ROW), colors['bg'])
            if pitch % 12 == 0:
                painter.setPen(QPen(colors['line'], 1))
                painter.drawLine(QLineF(self.LEFT, y + self.ROW, width, y + self.ROW))
            elif not self._compact and self.ROW >= 12:
                painter.setPen(QPen(colors['grid'], .65))
                painter.drawLine(QLineF(self.LEFT, y + self.ROW, width, y + self.ROW))
        # Seconds keep their meaning for MIDI files with tempo changes.
        tick = next((s for s in (.25, .5, 1, 2, 5, 10, 20) if s * self._zoom >= 55), 20)
        start_tick = max(0, math.floor(h / self._zoom / tick))
        for i in range(start_tick, math.ceil((h + width - self.LEFT) / self._zoom / tick) + 1):
            x = self.LEFT + i * tick * self._zoom - h
            if x < self.LEFT:
                continue
            painter.setPen(QPen(colors['grid'], .65, Qt.DotLine if self._compact else Qt.SolidLine))
            painter.drawLine(QLineF(x, self.RULER, x, height))
        for index in self._visible_indices():
            stored = self._notes[index]
            note = self._drag['preview'] if self._drag and self._drag['index'] == index else stored
            rect = self._rect(note)
            if rect.right() < self.LEFT or rect.left() > width or rect.bottom() < self.RULER or rect.top() > height:
                continue
            selected = index == self._selected and not self._compact
            # Keep the generous hit rectangle, but paint inside the true time
            # span. Symmetric gutters reveal repeated attacks without shifting
            # the note or its centered label; tiny notes cannot cover neighbors.
            body = QRectF(rect)
            body.setWidth((note['end'] - note['start']) * self._zoom)
            padding = min(1.5, body.width() * .18)
            body.adjust(padding, 0, -padding, 0)
            outline = min(1 if selected else .7, body.width() * .2)
            painter.setPen(QPen(colors['ink'] if selected else colors['accent'], outline))
            painter.setBrush(colors['selection'] if selected else colors['accent'])
            painter.drawRoundedRect(body, .8, .8)
            key_label = self._key_label(note['pitch'])
            if (not self._compact and rect.width() >= metrics.horizontalAdvance(key_label) + 8
                    and rect.height() >= metrics.tightBoundingRect(key_label).height() + 2):
                painter.setPen(colors['ink'] if selected else colors['accent_ink'])
                # Anchor to the whole note, not the clipped visible portion.
                # Explicit single-line centering prevents implicit alignment or
                # wrapping from shifting labels as the score scrolls or resizes.
                painter.drawText(rect.adjusted(4, 0, -4, 0), Qt.AlignCenter | Qt.TextSingleLine, key_label)
            if selected and rect.width() >= 9 and rect.height() >= 8:
                painter.setPen(QPen(colors['ink'], .8))
                painter.drawLine(QLineF(rect.right() - 3, rect.top() + 3,
                                       rect.right() - 3, rect.bottom() - 3))
        if self._position is not None:
            x = self.LEFT + self._position * self._zoom - h
            painter.setPen(QPen(colors['playhead'], 1.25))
            painter.drawLine(QLineF(x, self.RULER, x, height))
        painter.restore()

        # Paint the fixed pitch gutter last so scrolling notes never cover it.
        if not self._compact:
            self._paint_piano(painter, height)
        painter.fillRect(QRectF(0, 0, width, self.RULER), colors['surface'])
        painter.setPen(colors['muted'])
        if not self._compact:
            painter.drawText(QRectF(8, 0, 90, self.RULER), Qt.AlignVCenter, '钢琴')
            painter.drawText(QRectF(102, 0, self.LEFT - 102, self.RULER), Qt.AlignVCenter, '按键')
        for i in range(start_tick, math.ceil((h + width - self.LEFT) / self._zoom / tick) + 1):
            x = self.LEFT + i * tick * self._zoom - h
            if x >= self.LEFT:
                seconds = i * tick
                text = f'{seconds:g}s' if seconds < 60 else f'{int(seconds // 60)}:{seconds % 60:04.1f}'
                painter.drawText(QRectF(x + 4, 0, tick * self._zoom - 5, self.RULER), Qt.AlignVCenter, text)
        painter.setPen(QPen(colors['line'], .8))
        painter.drawLine(QLineF(0, self.RULER, width, self.RULER))
        if not self._compact:
            painter.drawLine(QLineF(self.LEFT, 0, self.LEFT, height))
        if self._position is not None:
            x = self.LEFT + self._position * self._zoom - h
            if self.LEFT <= x <= width:
                painter.setPen(Qt.NoPen)
                painter.setBrush(colors['playhead'])
                painter.drawConvexPolygon([QPointF(x - 3.5, self.RULER - 6), QPointF(x + 3.5, self.RULER - 6), QPointF(x, self.RULER)])
        if not self._notes:
            painter.setPen(colors['muted'])
            painter.drawText(QRectF(self.LEFT, self.RULER, width - self.LEFT, height - self.RULER),
                             Qt.AlignCenter, '旋律将在这里展开' if self._compact else '生成或打开工程后，在这里编辑旋律')

    @staticmethod
    def _pitch_label(pitch):
        return ('C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B')[pitch % 12] + str(pitch // 12 - 1)

    @staticmethod
    def _key_label(pitch):
        key = ',' if pitch >= 84 else ('Z', 'Z', 'X', 'X', 'C', 'V', 'V', 'B', 'B', 'N', 'N', 'M')[pitch % 12]
        return ('↓' if pitch < 60 else '↑' if pitch >= 72 else '') + key + ('♯' if pitch % 12 in (1, 3, 6, 8, 10) else '')

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip:
            index = self._hit(event.pos())
            if index is not None:
                note = self._notes[index]
                text = (f"音高：{self._pitch_label(note['pitch'])}  ·  演奏按键：{self._key_label(note['pitch'])}\n"
                        f"{note['start']:.2f}–{note['end']:.2f} 秒  ·  时长 {note['end']-note['start']:.2f} 秒")
                QToolTip.showText(event.globalPos(), text, self.viewport())
                event.accept();return True
        return super().viewportEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)
        pos = event.position()
        if pos.y() < self.RULER and pos.x() >= self.LEFT:
            seconds = self._time_at(pos.x())
            end = max((n['end'] for n in self._notes), default=0)
            if self._notes:
                self.seekRequested.emit(min(seconds, end))
            return
        self._selected = None if self._compact else self._hit(pos)
        if self._selected is not None and self._editable:
            note = self._notes[self._selected]
            rect = self._rect(note)
            mode = 'resize' if rect.right() - pos.x() <= min(7, rect.width() * .3) else 'move'
            self._drag = dict(index=self._selected, original=deepcopy(note), preview=deepcopy(note),
                              mode=mode, x=pos.x(), y=pos.y(), moved=False,
                              scroll_x=self._view_offset, scroll_y=self.verticalScrollBar().value())
            self._position = None
        self.viewport().update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._drag and self._editable:
            drag = self._drag
            dx = pos.x() - drag['x'] + self._view_offset - drag['scroll_x']
            dy = pos.y() - drag['y'] + self.verticalScrollBar().value() - drag['scroll_y']
            if abs(dx) + abs(dy) < 3 and not drag['moved']:
                return
            drag['moved'] = True
            original = drag['original']
            candidate = deepcopy(original)
            if drag['mode'] == 'resize':
                end = round((original['end'] + dx / self._zoom) / self.SNAP) * self.SNAP
                candidate['end'] = min(self.MAX_TIME, max(original['start'] + self.MIN_LENGTH, round(end, 6)))
            else:
                duration = original['end'] - original['start']
                # A vertical-only gesture must preserve the original, unsnapped timing.
                start = original['start'] if abs(dx) < 3 else round((original['start'] + dx / self._zoom) / self.SNAP) * self.SNAP
                candidate['start'] = min(self.MAX_TIME - duration, max(0, round(start, 6))) if abs(dx) >= 3 else original['start']
                candidate['end'] = candidate['start'] + duration
                candidate['pitch'] = max(self.LOW, min(self.HIGH, original['pitch'] - round(dy / self.ROW)))
            drag['preview'] = candidate
            self.viewport().update()
            return
        index = self._hit(pos)
        if index is not None and self._editable:
            rect = self._rect(self._notes[index])
            self.viewport().setCursor(Qt.SizeHorCursor if rect.right() - pos.x() <= min(7, rect.width() * .3) else Qt.SizeAllCursor)
        elif pos.y() < self.RULER and pos.x() >= self.LEFT and self._notes:
            self.viewport().setCursor(Qt.PointingHandCursor)
        else:
            self.viewport().unsetCursor()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._drag:
            drag = self._drag
            self._drag = None
            if drag['moved']:
                notes = self.get_notes()
                notes[drag['index']] = drag['preview']
                if self._commit(notes, drag['preview']):
                    self.message.emit('已修改音符。试听和导出将使用当前曲谱。')
            self.viewport().update()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.LeftButton or not self._editable:
            return super().mouseDoubleClickEvent(event)
        pos = event.position()
        self._drag = None
        if pos.x() < self.LEFT or pos.y() < self.RULER or self._hit(pos) is not None:
            return
        start = round(self._time_at(pos.x()) / self.SNAP) * self.SNAP
        start = min(self.MAX_TIME - .3, max(0, round(start, 6)))
        added = dict(pitch=self._pitch_at(pos.y()), start=start, end=round(start + .3, 6), velocity=80)
        notes = self.get_notes()
        notes.append(added)
        if self._commit(notes, added):
            self.message.emit('已添加音符。拖动右边可以调整时长。')

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._drag:
            self._drag = None
            self.viewport().update()
            return
        if event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_Z:
            self.redo() if event.modifiers() & Qt.ShiftModifier else self.undo()
        elif event.modifiers() & Qt.ControlModifier and event.key() == Qt.Key_Y:
            self.redo()
        elif event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selected()
        else:
            return super().keyPressEvent(event)
        event.accept()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if event.modifiers() & Qt.ControlModifier and event.modifiers() & Qt.ShiftModifier:
            self._set_pitch_zoom(self.ROW * (1.25 if delta > 0 else .8))
            event.accept()
        elif event.modifiers() & Qt.ControlModifier:
            self._set_zoom(self._zoom * (1.25 if delta > 0 else .8), event.position().x())
            event.accept()
        elif event.modifiers() & Qt.ShiftModifier:
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta)
            event.accept()
        else:
            super().wheelEvent(event)
