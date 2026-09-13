"""Discover local scores; the optional catalog enriches files, never limits them."""
from pathlib import Path
import hashlib
import json
import logging
from .paths import default_library_root

MIDI_SUFFIXES = {'.mid', '.midi', '.kar', '.rmi'}


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('曲库刷新已取消。')


def _file_digest(path, cancel):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        while True:
            _check_cancel(cancel)
            chunk = file.read(64 * 1024)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def sample_entries(folder=None, cancel=None, *, strict=False):
    """Discover files; strict scans surface IO failures instead of replacing good data."""
    _check_cancel(cancel)
    root = Path(folder) if folder else default_library_root()
    try:
        rows = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
        if not isinstance(rows, list):
            rows = []
    except (OSError, ValueError):
        rows = []
    metadata = {}
    for row in rows:
        _check_cancel(cancel)
        if isinstance(row, dict) and isinstance(row.get('file'), str):
            metadata[row['file'].casefold()] = row
    try:
        files = []
        for path in root.iterdir():
            _check_cancel(cancel)
            if path.is_file() and path.suffix.lower() in MIDI_SUFFIXES:
                files.append(path)
        files.sort(key=lambda p: (p.stem.casefold(), p.name.casefold()))
    except InterruptedError:
        raise
    except FileNotFoundError:
        return []
    except OSError:
        if strict:
            raise
        logging.warning('Cannot scan music folder: %s', root)
        return []
    entries = []
    for path in files:
        _check_cancel(cancel)
        row = metadata.get(path.name.casefold(), {})
        # A replaced file must not inherit another arrangement's track preset.
        if row:
            try:
                digest = row.get('sha256')
                if not isinstance(digest, str) or _file_digest(path, cancel) != digest.lower():
                    row = {}
            except InterruptedError:
                raise
            except FileNotFoundError:
                continue
            except OSError:
                if strict:
                    raise
                continue
        title = row.get('title')
        entries.append(dict(row, file=path.name, title=title[:200] if isinstance(title, str) and title else path.stem,
                            path=path.resolve()))
    _check_cancel(cancel)
    return entries
