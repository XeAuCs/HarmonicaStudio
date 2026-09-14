"""Stable managed project names; source fingerprinting runs in the load worker."""
import hashlib
import os
from pathlib import Path
from .project import load_project


def song_project_path(root, source, digest):
    identity = os.path.normcase(str(Path(source).resolve())) + '\n' + digest.lower()
    key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
    return Path(root) / (key + '.hstudio')


def find_song_project(root, source, cancel=None):
    digest = hashlib.sha256()
    with Path(source).open('rb') as file:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError('读取歌曲工程已取消。')
            chunk = file.read(64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    path = song_project_path(root, source, digest.hexdigest())
    try:
        project = load_project(path)
    except FileNotFoundError:
        return path, None
    except (OSError, ValueError) as exc:
        # Never replace an unreadable saved score with a fresh conversion.
        raise ValueError(f'无法打开自动工程，请检查或另存后重试：{path}') from exc
    return path, project
