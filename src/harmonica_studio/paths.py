from pathlib import Path
import os, sys


def resource_root():
    return Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]


def data_root():
    if os.environ.get('HARMONICA_STUDIO_HOME'):
        return Path(os.environ['HARMONICA_STUDIO_HOME']).resolve()
    if getattr(sys, 'frozen', False):
        exe = Path(sys.executable).parent
        return exe.parent.parent / 'data' if exe.parent.name == 'app' else exe / 'data'
    return resource_root() / 'data'


def template_path():
    return Path(__file__).parent / 'assets/player.ahk'

def icon_path():
    return Path(__file__).parent / 'assets/studio.ico'


def default_library_root():
    """Prefer the user's editable samples folder over frozen internal resources."""
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).resolve()
        if executable.parent.parent.name == 'app':
            return executable.parent.parent.parent / 'samples'
    return resource_root() / 'samples'
