"""Global appearance, library and playback preferences."""
from dataclasses import asdict, dataclass
from pathlib import Path
import json

THEME_KEYS = ('paper', 'forest', 'blue', 'plum')


@dataclass(frozen=True)
class Preferences:
    theme: str = 'paper'
    compact: bool = False
    library_folder: str = ''
    skip_long_rests: bool = True
    start_from_highlight: bool = False


def load_preferences(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            return Preferences()
        theme = data.get('theme', 'paper')
        compact = data.get('compact', False)
        folder = data.get('library_folder', '')
        skip = data.get('skip_long_rests', True)
        highlight = data.get('start_from_highlight', False)
        return Preferences(theme=theme if theme in THEME_KEYS else 'paper',
                           compact=compact if type(compact) is bool else False,
                           library_folder=folder if isinstance(folder, str) and '\0' not in folder else '',
                           skip_long_rests=skip if type(skip) is bool else True,
                           start_from_highlight=highlight if type(highlight) is bool else False)
    except (OSError, ValueError, TypeError):
        return Preferences()


def save_preferences(path, preferences):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(asdict(preferences), ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
