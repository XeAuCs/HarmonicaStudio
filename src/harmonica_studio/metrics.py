"""Optional, bounded timing observations; never store document content or paths."""
from collections import defaultdict
from contextlib import contextmanager
from functools import wraps
import math
from threading import Lock
import time


def summarize(values):
    ordered = sorted(values)
    if not ordered:
        return dict(count=0, mean_ms=None, p50_ms=None, p95_ms=None, max_ms=None)
    return dict(count=len(ordered), mean_ms=sum(ordered) / len(ordered),
                p50_ms=ordered[math.ceil(len(ordered) * .5) - 1],
                p95_ms=ordered[math.ceil(len(ordered) * .95) - 1], max_ms=ordered[-1])


class MetricsRecorder:
    def __init__(self, *, now=time.perf_counter, max_samples=20_000):
        if type(max_samples) is not int or max_samples < 1:
            raise ValueError('max_samples must be a positive integer')
        self.now = now
        self.max_samples = max_samples
        self._samples = []
        self._dropped = 0
        self._lock = Lock()

    def record(self, name, elapsed, *, outcome='ok', **context):
        sample = dict(name=name, duration_ms=max(0, elapsed * 1000), outcome=outcome, **context)
        with self._lock:
            if len(self._samples) < self.max_samples:
                self._samples.append(sample)
            else:
                self._dropped += 1

    @contextmanager
    def measure(self, name, **context):
        start = self.now()
        outcome = 'ok'
        try:
            yield
        except BaseException as exc:
            outcome = 'cancelled' if isinstance(exc, InterruptedError) else 'error'
            raise
        finally:
            self.record(name, self.now() - start, outcome=outcome, **context)

    def snapshot(self):
        with self._lock:
            samples = [dict(row) for row in self._samples]
            dropped = self._dropped
        grouped = defaultdict(list)
        for row in samples:
            grouped[row['name']].append(row['duration_ms'])
        return dict(samples=samples, summary={name: summarize(values) for name, values in grouped.items()},
                    dropped_samples=dropped)


def measured(operation):
    """Observe synchronous controller work, including synchronous view listeners."""
    @wraps(operation)
    def wrapper(self, *args, **kwargs):
        if self.metrics is None:
            return operation(self, *args, **kwargs)
        with self.metrics.measure('controller.' + operation.__name__, revision=self.state.revision):
            return operation(self, *args, **kwargs)
    return wrapper
