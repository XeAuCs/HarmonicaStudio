from pathlib import Path
import os, sys


def resource_root():
    return Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]


def application_root():
    """Portable files live beside the EXE; source runs use the repository."""
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else resource_root()


def data_root():
    if os.environ.get('HARMONICA_STUDIO_HOME'):
        return Path(os.environ['HARMONICA_STUDIO_HOME']).resolve()
    return application_root() / 'data'


def template_path():
    return Path(__file__).parent / 'assets/player.ahk'

def icon_path():
    return Path(__file__).parent / 'assets/studio.ico'


def default_library_root():
    """One editable library, independent of the launcher's working directory."""
    return application_root() / 'samples'


def library_path(setting):
    path = Path(setting) if setting else default_library_root()
    return path if path.is_absolute() else application_root() / path


def library_setting(folder):
    if not folder:
        return ''
    path = library_path(folder).resolve()
    if path == default_library_root().resolve():
        return ''
    try:
        return str(path.relative_to(application_root().resolve()))
    except ValueError:
        return str(path)
