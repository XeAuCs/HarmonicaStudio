"""Compare extraction on labelled examples; measure (not label) the local library.

Run with the project Python. No audio is rendered and no user files are changed.
"""
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from harmonica_studio.melody import simplify, prepare, rank_parts
from harmonica_studio.midi import read_midi
from harmonica_studio.models import Options
from harmonica_studio.notes import normalize_score_notes


def note(pitch, start, end):
    return dict(pitch=pitch, start=start, end=end, velocity=80)


def evaluate():
    held = [note(72, 0, 2), note(74, 2, 2.5)]
    middle = [note(p, i*.5, i*.5+.5) for i, p in enumerate([72, 74, 76, 74])]
    fast = [note(60, 0, .02), note(84, .02, .04), note(48, .04, .06)]
    rests = [note(60, 0, .4), note(79, 2, 2.5)]
    cases = {
        'held_note_lower_accompaniment': (held + [note(67, .5, .8), note(65, 1, 1.3)], held),
        'middle_voice_high_ornaments': (middle + [note(91, i*.5+.01, i*.5+.06) for i in range(4)], middle),
        'fast_notes_large_leaps': (fast, fast),
        'rests': (rests, rests),
        'legato': ([note(72, 0, .52), note(74, .5, 1.02), note(76, 1, 1.5)],
                   [note(72, 0, .5), note(74, .5, 1), note(76, 1, 1.5)]),
    }
    labelled = []
    for name, (source, expected) in cases.items():
        for mode in ('highest', 'sustain', 'continuous'):
            result = simplify(source, mode, trim=False)
            gold = {(n['pitch'], n['start']) for n in expected}
            actual = {(n['pitch'], n['start']) for n in result}
            matches = len(gold & actual)
            truncated = sum(any(n['pitch'] == g['pitch'] and n['start'] == g['start']
                               and n['end'] < g['end'] - 1e-9 for n in result) for g in expected)
            labelled.append(dict(case=name, mode=mode, expected_notes=len(gold),
                                 missing=len(gold-actual), extra=len(actual-gold),
                                 precision=matches/max(1, len(actual)), recall=matches/len(gold),
                                 truncated=truncated, exact=result == expected))
    library = []
    for path in sorted((ROOT / 'samples').glob('*.mid')):
        parts, names = read_midi(path)
        row = dict(file=path.name, source_notes=sum(map(len, parts.values())), modes={})
        start = perf_counter()
        row['recommended_part'] = rank_parts(parts, names)[0][0]
        row['ranking_seconds'] = perf_counter() - start
        for mode in ('sustain', 'continuous'):
            start = perf_counter()
            notes, report = prepare(parts, names, Options(melody_mode=mode, phrase_octave=True))
            elapsed = perf_counter() - start
            normalize_score_notes(notes)
            row['modes'][mode] = dict(seconds=elapsed, notes=len(notes),
                                     dropped=report['dropped_out_of_range'],
                                     phrase_adjusted=report['phrase_adjusted_notes'])
        library.append(row)
    start = perf_counter()
    stress = [note(60 + i % 12, i*.01, i*.01+.009) for i in range(100000)]
    extracted = simplify(stress, 'continuous', trim=False)
    stress_result = dict(input_notes=len(stress), output_notes=len(extracted), seconds=perf_counter()-start)
    assert extracted == stress
    return dict(limitations='Constructed labelled examples only; library has no ground-truth melody labels. No general accuracy claim.',
                labelled=labelled, library=library, stress=stress_result)


if __name__ == '__main__':
    result = evaluate()
    target = ROOT / 'verification' / 'melody-evaluation.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(labelled_exact={mode:sum(r['exact'] for r in result['labelled'] if r['mode']==mode)
                                         for mode in ('highest','sustain','continuous')},
                          library_files=len(result['library']), stress=result['stress']), ensure_ascii=False))
