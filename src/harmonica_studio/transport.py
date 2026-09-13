"""Smooth visual transport, periodically corrected by the actual audio device."""
import time
import bisect
from .rests import LONG_REST_THRESHOLD, RETAINED_REST, REST_EPSILON


def playback_anchors(notes, actual):
    """Align note attacks without stopping score time during key-release gaps.

    The scheduler shortens a note to release keys before the next attack. That
    gap is articulation, not extra score time: mapping both adjacent endpoints
    would turn a shared score boundary into a flat interval in the inverse map.
    Interpolate between attacks instead, retaining the final release as an end
    anchor. Before a shortened long rest, retain that note's release as well:
    the cursor traverses only the silent region faster, never a sounding note.
    """
    pairs = list(zip(notes, actual))
    anchors = [(note['start'], played['start']) for note, played in pairs]
    for (note, played), (following, next_played) in zip(pairs, pairs[1:]):
        score_gap = following['start'] - note['end']
        audio_gap = next_played['start'] - played['end']
        # Event times round to milliseconds. Uncompressed long rests do not
        # qualify; short key-release gaps keep their smooth onset-only mapping.
        if score_gap > LONG_REST_THRESHOLD + REST_EPSILON and audio_gap <= RETAINED_REST + .002:
            anchors.append((note['end'], played['end']))
    if pairs:
        note, played = pairs[-1]
        anchors.append((note['end'], played['end']))
    return anchors


class TimeMap:
    """Build the score/audio map once; locate positions in logarithmic time."""
    def __init__(self, anchors=()):
        self.points = sorted(dict(anchors).items())
        self.xs = [point[0] for point in self.points]

    def __call__(self, value):
        if not self.points:
            return max(0.0, value)
        if value < self.xs[0]:
            return max(0.0, self.points[0][1] + value - self.xs[0])
        index = bisect.bisect_right(self.xs, value) - 1
        x, y = self.points[index]
        if index == len(self.points) - 1:
            return y + value - x
        x2, y2 = self.points[index + 1]
        return y + (value - x) / (x2 - x) * (y2 - y)


class PlaybackClock:
    def __init__(self, now=time.perf_counter):
        self._now = now
        self.reset()

    def reset(self, position=0.0, playing=False):
        self._position = max(0.0, float(position))
        self._sampled_at = self._now()
        self._playing = bool(playing)
        self._last = self._position
        self._correction = 0.0

    def synchronize(self, position, playing, *, discontinuity=False):
        now = self._now()
        actual = max(0.0, float(position))
        estimated = self.position(now=now)
        if discontinuity or not playing or not self._playing or abs(actual - estimated) > .25:
            self.reset(actual, playing)
            return
        # Apply small device corrections over one polling interval, not as steps.
        self._position = estimated
        self._sampled_at = now
        self._correction = actual - estimated
        self._playing = True

    def position(self, *, now=None, duration=None):
        now = self._now() if now is None else now
        elapsed = max(0.0, now - self._sampled_at) if self._playing else 0.0
        value = self._position + elapsed + self._correction * min(1.0, elapsed / .12)
        # Tiny timing corrections cannot move the cursor backwards mid-play.
        if self._playing:
            value = max(self._last, value)
        if duration is not None:
            value = min(max(0.0, duration), value)
        self._last = value
        return value
