"""Canonical monophonic scores; raw MIDI may have a wider range and chords."""
import math
from typing import TypedDict

MIN_PITCH = 48
MAX_PITCH = 85
MAX_NOTES = 100_000
MAX_SECONDS = 1200
TIME_EPSILON = 1e-8
DEFAULT_VELOCITY = 80


class Note(TypedDict):
    pitch: int
    start: float
    end: float
    velocity: int


def normalize_score_notes(notes, *, max_notes=MAX_NOTES) -> list[Note]:
    """Validate and return an independent, sorted, idempotent canonical copy."""
    if not isinstance(notes, list) or len(notes) > max_notes:
        raise ValueError(f'工程音符列表无效，最多支持 {max_notes} 个音符。')
    normalized = []
    for index, note in enumerate(notes, 1):
        if not isinstance(note, dict):
            raise ValueError(f'第 {index} 个音符格式无效。')
        pitch, velocity = note.get('pitch'), note.get('velocity', DEFAULT_VELOCITY)
        start, end = note.get('start'), note.get('end')
        if type(pitch) is not int or not MIN_PITCH <= pitch <= MAX_PITCH:
            raise ValueError(f'第 {index} 个音符超出口琴音域（{MIN_PITCH} 至 {MAX_PITCH}）。')
        if type(velocity) is not int or not 1 <= velocity <= 127:
            raise ValueError(f'第 {index} 个音符力度须为 1 至 127。')
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not 0 <= start < end <= MAX_SECONDS
                or not math.isfinite(start) or not math.isfinite(end)):
            raise ValueError(f'第 {index} 个音符时间无效，结束时间须晚于开始且在 20 分钟以内。')
        normalized.append(dict(pitch=pitch, start=start, end=end, velocity=velocity))
    normalized.sort(key=lambda n: (n['start'], n['pitch']))
    for first, second in zip(normalized, normalized[1:]):
        if first['end'] > second['start']:
            if first['end'] - second['start'] <= TIME_EPSILON and second['start'] > first['start']:
                first['end'] = second['start']
            else:
                raise ValueError('音符存在重叠；口琴一次只能演奏一个音，请移开或缩短音符。')
    return normalized
