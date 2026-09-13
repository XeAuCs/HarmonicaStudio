"""Discover local scores; the optional catalog enriches files, never limits them."""
from pathlib import Path
import hashlib
import json
import logging
from .paths import default_library_root

MIDI_SUFFIXES = {'.mid', '.midi', '.kar', '.rmi'}


def sample_entries(folder=None):
    root = Path(folder) if folder else default_library_root()
    try:
        rows = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
        if not isinstance(rows, list):
            rows = []
    except (OSError, ValueError):
        rows = []
    metadata = {}
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get('file'), str):
            metadata[row['file'].casefold()] = row
    try:
        files = sorted((p for p in root.iterdir() if p.is_file() and p.suffix.lower() in MIDI_SUFFIXES),
                       key=lambda p: (p.stem.casefold(), p.name.casefold()))
    except OSError:
        logging.warning('Cannot scan music folder: %s', root)
        return []
    entries = []
    for path in files:
        row = metadata.get(path.name.casefold(), {})
        # A replaced file must not inherit another arrangement's track preset.
        if row:
            try:
                digest = row.get('sha256')
                if not isinstance(digest, str) or hashlib.sha256(path.read_bytes()).hexdigest() != digest.lower():
                    row = {}
            except OSError:
                continue
        title = row.get('title')
        entries.append(dict(row, file=path.name, title=title[:200] if isinstance(title, str) and title else path.stem,
                            path=path.resolve()))
    return entries
