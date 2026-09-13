"""Serial snapshot persistence, with coalescing only between adjacent autosaves."""
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .jobs import JobKind, JobRunner
from .project import save_project


class SaveKind(str, Enum):
    AUTO = 'auto'
    MANUAL = 'manual'
    PRESERVE = 'preserve'
    CLOSE = 'close'


@dataclass(frozen=True)
class SaveSnapshot:
    document: int
    revision: int
    project: dict | None


@dataclass(frozen=True)
class SaveRequest:
    number: int
    snapshot: SaveSnapshot
    paths: tuple[Path, ...]
    kind: SaveKind
    remote: bool = False


@dataclass(frozen=True)
class SaveCompletion:
    request: SaveRequest
    error: Exception | None = None
    job: object = None


def write_snapshot(request):
    # All paths use the existing validation and atomic replacement contract.
    # A preservation batch may leave a recovery copy if its autosave fails;
    # the controller must still keep the current document in that case.
    for path in request.paths:
        save_project(path, request.snapshot.project)


class SaveCoordinator:
    MAX_PENDING = 16

    def __init__(self, executor=None, *, metrics=None):
        self.jobs = JobRunner(executor, metrics=metrics)
        self.pending = deque()
        self.active = None
        self._failure = None
        self._number = 0

    @property
    def busy(self):
        return self.active is not None or bool(self.pending)

    def submit(self, snapshot, paths, kind, *, remote=False):
        if self.jobs.closed:
            raise RuntimeError('保存队列已关闭。')
        paths = tuple(Path(path) for path in paths)
        tail = self.pending[-1] if self.pending else None
        merge = (kind == SaveKind.AUTO and tail is not None and tail.kind == kind
                 and tail.snapshot.document == snapshot.document and tail.paths == paths)
        if not merge and len(self.pending) >= self.MAX_PENDING:
            raise RuntimeError('待保存请求较多，请稍候再试。')
        self._number += 1
        request = SaveRequest(self._number, snapshot, paths, kind, remote)
        if merge:
            self.pending[-1] = request
        else:
            self.pending.append(request)
        self.start_next()
        return request

    def start_next(self):
        if self.active is not None or not self.pending or self.jobs.closed:
            return
        self.active = self.pending.popleft()
        try:
            self.jobs.start(JobKind.SAVE, self.active.snapshot.revision,
                            write_snapshot, self.active, cancellable=False,
                            remote=self.active.remote)
        except Exception as exc:
            self._failure = exc

    def take_completed(self):
        if self._failure is not None:
            result = SaveCompletion(self.active, self._failure)
            self.active, self._failure = None, None
            return result
        job = self.jobs.take_completed()
        if job is None:
            return None
        request, self.active = self.active, None
        try:
            job.future.result()
        except Exception as exc:
            return SaveCompletion(request, exc, job)
        return SaveCompletion(request, job=job)

    def close(self):
        if self.busy:
            raise RuntimeError('仍有工程等待保存。')
        self.jobs.close()
