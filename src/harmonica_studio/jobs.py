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


class JobRunner:
    def __init__(self, executor=None):
        self.executor = executor if executor is not None else ThreadPoolExecutor(max_workers=1)
        self.current = None
        self._number = 0
        self.closed = False

    def start(self, kind, revision, function, *args, follow_up=FollowUp.NONE,
              prepare=False, remote=False, cancellable=True):
        if self.closed or self.current is not None:
            raise RuntimeError('已有任务正在处理或应用已关闭。')
        cancel = Event()
        future = self.executor.submit(function, *args, cancel) if cancellable else self.executor.submit(function, *args)
        self._number += 1
        self.current = Job(self._number, kind, revision, cancel, future, follow_up, prepare, remote)
        return self.current

    def take_completed(self):
        if self.closed or self.current is None or not self.current.future.done():
            return None
        job, self.current = self.current, None
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
