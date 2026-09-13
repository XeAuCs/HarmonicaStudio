"""One worker; only the owning thread may publish completed results."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from threading import Event
from .app_state import FollowUp


class JobKind(str, Enum):
    LOAD = 'load'
    CONVERT = 'convert'
    EXPORT = 'export'


@dataclass
class Job:
    number: int
    kind: JobKind
    revision: int
    cancel: Event
    future: object
    follow_up: FollowUp = FollowUp.NONE
    prepare: bool = False
    remote: bool = False
    finished_at: float | None = None


class JobRunner:
    def __init__(self, executor=None, *, metrics=None, before_work=None):
        self.executor = executor if executor is not None else ThreadPoolExecutor(max_workers=1)
        self.metrics = metrics
        self.before_work = before_work
        self.current = None
        self._number = 0
        self.closed = False

    def start(self, kind, revision, function, *args, follow_up=FollowUp.NONE,
              prepare=False, remote=False, cancellable=True):
        if self.closed or self.current is not None:
            raise RuntimeError('已有任务正在处理或应用已关闭。')
        cancel = Event()
        job = Job(self._number + 1, kind, revision, cancel, None, follow_up, prepare, remote)
        arguments = (*args, cancel) if cancellable else args
        if self.metrics is None and self.before_work is None:
            future = self.executor.submit(function, *arguments)
        else:
            metrics = self.metrics
            submitted = metrics.now() if metrics is not None else None
            context = dict(job=job.number, kind=kind.value, revision=revision)
            def work():
                if self.before_work is not None:
                    self.before_work(kind, cancel)
                return function(*arguments)
            def execute():
                if metrics is None:
                    return work()
                metrics.record('job.queue', metrics.now() - submitted, **context)
                try:
                    with metrics.measure('job.execute', **context):
                        return work()
                finally:
                    job.finished_at = metrics.now()
            future = self.executor.submit(execute)
        job.future = future
        self._number = job.number
        self.current = job
        return self.current

    def take_completed(self):
        if self.closed or self.current is None or not self.current.future.done():
            return None
        job, self.current = self.current, None
        if self.metrics is not None and job.finished_at is not None:
            self.metrics.record('job.delivery', self.metrics.now() - job.finished_at,
                                job=job.number, kind=job.kind.value, revision=job.revision)
        return job

    def cancel(self):
        if self.current:
            self.current.follow_up = FollowUp.NONE
            self.current.cancel.set()

    def clear_follow_up(self, action):
        if self.current and self.current.follow_up == action:
            self.current.follow_up = FollowUp.NONE

    def close(self):
        self.cancel()
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.current = None
