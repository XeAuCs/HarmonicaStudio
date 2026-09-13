"""Derive a shorter performance without changing the editable score."""

LONG_REST_THRESHOLD = 3.0
RETAINED_REST = 0.6
REST_EPSILON = 1e-8


def compress_long_rests(notes, enabled=False):
    """Return independent notes, the number of shortened rests and seconds saved.

    Only silence between notes is eligible. Leading silence, sounding notes and
    ordinary phrasing keep their timing; every later note receives the same
    cumulative shift. Always start from canonical score notes on each export.
    """
    if type(enabled) is not bool:
        raise ValueError('跳过长空白设置必须是布尔值。')
    shifted, removed, count = [], 0.0, 0
    previous_end = None
    for note in notes:
        if enabled and previous_end is not None:
            gap = note['start'] - previous_end
            if gap > LONG_REST_THRESHOLD + REST_EPSILON:
                removed += gap - RETAINED_REST
                count += 1
        shifted.append(dict(note, start=note['start'] - removed,
                            end=note['end'] - removed))
        previous_end = note['end']
    return shifted, count, removed
