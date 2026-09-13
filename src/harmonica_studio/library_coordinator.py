"""One library worker plus one replaceable pending request; no application state."""
from dataclasses import dataclass
from pathlib import Path

from .jobs import JobKind, JobRunner
from .library import sample_entries


@dataclass(frozen=True)
class LibraryRequest:
    generation: int
    root: Path
    remote: bool = False


def scan_library(root, cancel):
    return sample_entries(root, cancel, strict=True)


class LibraryCoordinator:
    def __init__(self, executor=None, *, metrics=None):
        self.jobs = JobRunner(executor, metrics=metrics)
        self.latest = self.active = self.pending = None
        self._generation = 0

    @property
    def busy(self):
        return self.jobs.current is not None or self.pending is not None

    def request(self, root, *, remote=False):
        if self.jobs.closed:
            raise RuntimeError('应用已关闭。')
        self._generation += 1
        request = LibraryRequest(self._generation, Path(root), remote)
        if self.jobs.current is None:
            self._start(request)
        else:
            # Same-directory notifications may describe changes made mid-scan.
            # Keep one fresh scan, without repeatedly interrupting the active one.
            self.pending = request
            if self.active.root != request.root:
                self.jobs.cancel()
        self.latest = request
        return request

    def _start(self, request):
        self.jobs.start(JobKind.LIBRARY, request.generation, scan_library, request.root,
                        remote=request.remote)
        self.active = request

    def take_completed(self):
        job = self.jobs.take_completed()
        if job is None:
            return None
        request, self.active = self.active, None
        return request, job

    def start_pending(self):
        if self.pending is not None and self.jobs.current is None:
            request, self.pending = self.pending, None
            self._start(request)

    def close(self):
        self.pending = None
        self.jobs.close()
        self.active = None
