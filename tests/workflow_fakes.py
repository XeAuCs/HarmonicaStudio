"""Silent dependencies shared by headless and desktop application tests."""
from concurrent.futures import Future
from pathlib import Path


class ControlledExecutor:
    """Execute actual submitted work only when the test chooses to finish it."""
    def __init__(self):
        self.pending = []

    def submit(self, function, *args, **kwargs):
        future = Future()
        self.pending.append((future, function, args, kwargs))
        return future

    def finish_next(self):
        future, function, args, kwargs = self.pending.pop(0)
        if future.set_running_or_notify_cancel():
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

    def shutdown(self, wait=True, *, cancel_futures=False):
        if cancel_futures:
            for future, _, _, _ in self.pending:
                future.cancel()
            self.pending.clear()


class SilentScriptPlayer:
    """Observe performance requests without starting AHK or sending input."""
    def __init__(self):
        self.alive = False
        self.calls = []
        self.status = dict(state='idle', position=0, duration=0, message='')

    def play(self, script):
        self.calls.append(('play', Path(script)))
        self.alive = True
        self.status['state'] = 'countdown'

    def start(self, script):
        self.calls.append(('arm', Path(script)))
        self.alive = True
        self.status['state'] = 'ready'

    def stop_playback(self):
        self.calls.append(('stop_playback',))
        self.status['state'] = 'ready' if self.alive else 'idle'

    def stop(self):
        self.calls.append(('stop',))
        self.alive = False
        self.status['state'] = 'idle'

    def reap(self):
        pass

