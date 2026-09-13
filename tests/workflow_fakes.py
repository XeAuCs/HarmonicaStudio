"""Compatibility imports for existing workflow tests."""
from harmonica_studio.diagnostic_backends import ControlledExecutor, SilentScriptPlayer


def finish_saves(controller, executor):
    """Drive the real save pipeline through its shared controllable executor."""
    for _ in range(64):
        if not controller.saving:
            return
        if executor.pending:
            executor.finish_next()
        controller.poll()
    raise AssertionError('Save queue did not drain')
