"""Self-contained editable scores, separate from physical key scheduling."""
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import uuid

SCHEMA_VERSION = 1
MAX_FILE_BYTES = 20 * 1024 * 1024
from .notes import MAX_NOTES, normalize_score_notes


def make_project(notes, title='曲谱', source=None, options=None, report=None):
    project = {'schema_version': SCHEMA_VERSION, 'title': title, 'notes': notes}
    for name, value in (('source', source), ('options', options), ('report', report)):
        if value is not None:
            project[name] = asdict(value) if is_dataclass(value) else value
    return validate_project(project)


def _json_bytes(project):
    try:
        raw = json.dumps(project, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8')
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise ValueError('工程包含无效的文字、数字或附加信息。') from exc
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('工程文件不能超过 20 MB。')
    return raw


def validate_project(project):
    """Return an independent canonical copy; never adjust physical lead-in here."""
    if not isinstance(project, dict):
        raise ValueError('工程格式无效，应为口琴工坊工程文件。')
    if type(project.get('schema_version')) is not int or project['schema_version'] != SCHEMA_VERSION:
        raise ValueError('不支持此工程版本，请使用版本 1 的 .hstudio 工程。')
    title = project.get('title', '曲谱')
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError('工程名称须为 1 至 200 个字符。')
    normalized = normalize_score_notes(project.get('notes'), max_notes=MAX_NOTES)
    result = {'schema_version': SCHEMA_VERSION, 'title': title.strip(), 'notes': normalized}
    for name in ('source', 'options', 'report'):
        if name in project and project[name] is not None:
            value = project[name]
            if name == 'source' and not isinstance(value, (str, dict)):
                raise ValueError('工程来源信息无效。')
            if name in ('options', 'report') and not isinstance(value, dict):
                raise ValueError('工程设置或报告格式无效。')
            try:
                result[name] = deepcopy(value)
            except RecursionError as exc:
                raise ValueError('工程附加信息层级过深。') from exc
    _json_bytes(result)
    return result


def save_project(path, project):
    """Atomically replace one project file, leaving a previous save intact on failure."""
    path = Path(path)
    raw = _json_bytes(validate_project(project))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(raw)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_project(path):
    path = Path(path)
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError('工程文件不能超过 20 MB。')
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('工程文件不能超过 20 MB。')
    try:
        project = json.loads(raw.decode('utf-8-sig'))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('工程文件损坏或不是有效的 .hstudio 文件。') from exc
    return validate_project(project)
