"""Transactional conversion; no input injection and no network access."""
from dataclasses import asdict, dataclass
from pathlib import Path
from datetime import datetime
import hashlib, json, logging, re, shutil, uuid
from . import __version__
from .midi import read_midi, write_midi
from .melody import prepare, rank_parts
from .models import Options
from .schedule import build_events
from .preview import decode_events, render_wav
from .paths import template_path
from .project import make_project, validate_project, save_project, load_project
from .rests import compress_long_rests
from .transport import TimeMap, playback_anchors
from .song_projects import find_song_project


@dataclass(frozen=True)
class LoadedParts:
    parts: dict
    names: dict
    keys: list
    project_path: Path | None = None
    project: dict | None = None


@dataclass(frozen=True)
class PreparedExport:
    """Worker-owned data transferred to the controller after completion.

    Nested values are independent of the input project, not deeply immutable.
    The worker must not touch them after returning the result.
    """
    folder: Path
    report: dict
    project: dict
    actual: list
    anchors: list
    to_audio: TimeMap
    to_score: TimeMap


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('转换已取消。')


def load_ranked_midi(source, cancel=None, *, project_root=None):
    _check_cancel(cancel)
    project_path, project = (find_song_project(project_root, source, cancel)
                             if project_root is not None else (None, None))
    parts, names = read_midi(source)
    _check_cancel(cancel)
    keys = [key for key, _ in rank_parts(parts, names)]
    _check_cancel(cancel)
    return LoadedParts(parts, names, keys, project_path, project)


def _prepare_export(folder, report, project, actual):
    anchors = playback_anchors(project['notes'], actual)
    return PreparedExport(Path(folder), report, project, actual, anchors,
                          TimeMap(anchors), TimeMap((b, a) for a, b in anchors))


def load_export_result(folder, report):
    """Compatibility entry for explicitly opening existing export artifacts."""
    folder = Path(folder)
    project = load_project(folder / '工程.hstudio')
    actual = json.loads((folder / '音符.json').read_text(encoding='utf-8'))
    return _prepare_export(folder, report, project, actual)


def convert(source, output_root, options=None, cancel=None):
    """CLI-compatible full export returning (folder, report)."""
    return _convert(source, output_root, options, cancel, prepared=False)


def convert_prepared(source, output_root, options=None, cancel=None):
    return _convert(source, output_root, options, cancel, prepared=True)


def _convert(source, output_root, options, cancel, *, prepared):
    options = options or Options()
    options.validate()
    source, output_root = Path(source).resolve(), Path(output_root).resolve()
    parts, names = read_midi(source)
    notes, report = prepare(parts, names, options)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    report.update(source=str(source), source_sha256=source_hash, options=asdict(options))
    project = make_project(notes, source.stem, source={'path': str(source), 'sha256': source_hash},
                           options=asdict(options), report=report)
    return _export(project, output_root, cancel, 'midi_conversion', prepared=prepared)


def export_project(project, output_root, cancel=None):
    """Export a saved/edited score without reopening or reconverting its source MIDI."""
    return _export(project, output_root, cancel, 'edited_project')


def export_project_prepared(project, output_root, cancel=None):
    return _export(project, output_root, cancel, 'edited_project', prepared=True)


def _export(project, output_root, cancel, export_type, *, prepared=False):
    project = validate_project(project)
    notes = project['notes']
    if not notes:
        raise ValueError('工程还没有音符，请先添加音符再导出。')
    output_root = Path(output_root).resolve()
    report = dict(project.get('report', {}))
    for name, value in dict(source_notes=len(notes), removed_polyphony=0, dropped_out_of_range=0,
                            transpose_semitones=0, speed=1.0).items():
        report.setdefault(name, value)
    skip_long_rests = project.get('options', {}).get('skip_long_rests', False)
    performance, skipped_rests, removed_rest_seconds = compress_long_rests(notes, skip_long_rests)
    events, delayed = build_events(performance)
    actual = decode_events(events)
    if [n['pitch'] for n in actual] != [n['pitch'] for n in notes]:
        raise ValueError('按键和音符校验不一致，已中止导出。')
    label = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', project['title']).strip(' .')[:60] or '曲谱'
    if label.upper() in {'CON','PRN','AUX','NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        label = '曲谱_' + label
    suffix = datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    folder = output_root / (label+'-'+suffix)
    temp = output_root / ('.partial-'+uuid.uuid4().hex)
    output_root.mkdir(parents=True, exist_ok=True)
    temp.mkdir()
    try:
        if cancel and cancel.is_set():
            raise InterruptedError('转换已取消。')
        report.update(version=__version__, melody_notes=len(notes), current_notes=len(notes),
                      options=project.get('options', report.get('options', {})),
                      export_type=export_type, edited=export_type == 'edited_project',
                      delayed_notes=delayed, duration_seconds=round(events[-1][0]/1000,3),
                      skip_long_rests=skip_long_rests, skipped_long_rests=skipped_rests,
                      removed_rest_seconds=round(removed_rest_seconds, 6),
                      preview='合成音色；按导出的按键时间表渲染；不是游戏实录。')
        template = template_path().read_text(encoding='utf-8-sig')
        script = template.replace('__EVENTS__','\n'.join(','.join(map(str,e)) for e in events))
        (temp/'演奏脚本.ahk').write_text(script, encoding='utf-8-sig')
        # The MIDI and preview share the physical timing of the exported script.
        write_midi(actual,temp/'口琴单旋律.mid')
        (temp/'音符.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2),encoding='utf-8')
        (temp/'按键时间表.json').write_text(json.dumps(events),encoding='utf-8')
        (temp/'转换报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        project['report'] = report
        save_project(temp/'工程.hstudio', project)
        render_wav(events,temp/'试听.wav',cancel)
        result = _prepare_export(folder, report, project, actual) if prepared else (folder, report)
        if cancel and cancel.is_set():
            raise InterruptedError('转换已取消。')
        temp.rename(folder)
        logging.info('Exported %s: %s',project['title'],report)
        return result
    except BaseException:
        # Only remove the fresh staging directory owned by this invocation.
        assert temp.parent == output_root and temp.name.startswith('.partial-')
        shutil.rmtree(temp)
        raise
