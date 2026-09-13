"""Deterministic melody heuristics; no claim of perfect voice separation.

Continuous extraction uses a bounded dynamic-programming beam (32 paths).
Scores are preferences, not calibrated melody probabilities.
"""
from collections import defaultdict
from statistics import median
from .notes import MIN_PITCH, MAX_PITCH, MAX_SECONDS


OCTAVE_SHIFTS = tuple(range(-144, 145, 12))


def onset_groups(ordered):
    """Group close, overlapping attacks, never consecutive short notes."""
    group = []
    earliest_end = 0
    for n in ordered:
        if group and (n['start'] - group[0]['start'] > .025 or
                      n['start'] >= earliest_end):
            yield group
            group = []
        earliest_end = min(earliest_end, n['end']) if group else n['end']
        group.append(n)
    if group:
        yield group


def continuous_line(ordered):
    # Linked backpointers avoid copying a growing score for each hypothesis.
    nodes, scores, parents = [], [], []
    beam = [-1]
    low = min(n['pitch'] for n in ordered)
    span = max(12, max(n['pitch'] for n in ordered) - low)
    for group in onset_groups(ordered):
        additions = []
        for n in group:
            duration = n['end'] - n['start']
            reward = 2 + 2 * min(duration, 2) + .8 * (n['pitch'] - low) / span
            reward += .2 * n['velocity'] / 127
            best, parent = reward, -1
            for previous in beam:
                if previous < 0:
                    continue
                p = nodes[previous]
                interval = abs(n['pitch'] - p['pitch'])
                cost = .06 * min(interval, 24)
                overlap = p['end'] - n['start']
                if overlap > 0:
                    # Losing a sustained note costs more than a small legato overlap.
                    fraction = min(1, overlap / max(.001, p['end'] - p['start']))
                    cost += 8 * fraction
                    if n['end'] <= p['end'] and n['pitch'] < p['pitch']:
                        cost += 4
                else:
                    cost += .1 * min(-overlap, 3)
                candidate = scores[previous] + reward - cost
                if candidate > best:
                    best, parent = candidate, previous
            additions.append(len(nodes))
            nodes.append(n); scores.append(best); parents.append(parent)
        # Keeping previous states is the explicit skip-accompaniment/rest choice.
        beam = sorted((i for i in beam + additions if i >= 0),
                      key=lambda i: (-scores[i], i))[:32]
    selected = []
    current = beam[0]
    while current >= 0:
        selected.append(nodes[current])
        current = parents[current]
    return selected[::-1]


def simplify(notes, mode='sustain', trim=True):
    ordered = sorted(notes, key=lambda n: (n['start'], n['pitch']))
    if not ordered:
        return []
    if mode == 'continuous':
        selected = continuous_line(ordered)
    else:
        selected = []
    for group in (() if mode == 'continuous' else onset_groups(ordered)):
        chosen = dict(max(group, key=lambda n: (n['pitch'], n['velocity'])))
        if mode == 'sustain' and selected:
            prev = selected[-1]
            # Discard a low accompaniment attack entirely contained under a sustained melody.
            if (prev['pitch'] - chosen['pitch'] >= 7 and
                    chosen['start'] < prev['end'] - .025 and chosen['end'] <= prev['end'] + .025):
                continue
        selected.append(chosen)
    origin = selected[0]['start'] if trim else 0
    result = []
    for i, n in enumerate(selected):
        end = min(n['end'], selected[i+1]['start']) if i+1 < len(selected) else n['end']
        if end > n['start']:
            result.append(dict(n, start=n['start']-origin, end=end-origin))
    return result


def part_features(notes, song_start, song_end):
    events = defaultdict(int)
    for n in notes:
        events[n['start']] += 1
        events[n['end']] -= 1
    active = 0
    last = min(events)
    sounding = overlapping = 0
    for time, change in sorted(events.items()):
        if active:
            sounding += time - last
        if active > 1:
            overlapping += time - last
        active += change
        last = time
    line = simplify(notes, mode='highest', trim=False)
    intervals = [abs(a['pitch'] - b['pitch']) for a, b in zip(line, line[1:])]
    return dict(monophony=1 - overlapping / max(sounding, .001),
                coverage=sounding / max(song_end - song_start, .001),
                continuity=sum(max(0, 1 - i / 24) for i in intervals) / max(1, len(intervals)),
                register=max(0, min(1, (median(n['pitch'] for n in notes) - 36) / 48)),
                duration=min(1, median(n['end'] - n['start'] for n in notes) / .25))


def rank_parts(parts, names):
    ranked = []
    populated = {key: notes for key, notes in parts.items() if notes}
    if not populated:
        return []
    song_start = min(n['start'] for notes in populated.values() for n in notes)
    song_end = max(n['end'] for notes in populated.values() for n in notes)
    for key, notes in populated.items():
        label = names.get(key[0], '').lower()
        named = any(s in label for s in ('melody', 'vocal', 'lead', '主旋律', '人声'))
        features = part_features(notes, song_start, song_end)
        reachable = max(sum(MIN_PITCH <= n['pitch']+s <= MAX_PITCH for n in notes) for s in OCTAVE_SHIFTS) / len(notes)
        score = (.8*named + 3*features['monophony'] + features['coverage'] + .8*reachable
                 + .6*features['continuity'] + .5*features['register'] + .3*features['duration'])
        ranked.append((key, score))
    return sorted(ranked, key=lambda x: (-x[1], x[0]))


def note_weights(notes):
    typical = max(.001, median(n['end'] - n['start'] for n in notes))
    return [.5 + .5 * min(4, (n['end'] - n['start']) / typical) for n in notes]


def fit_phrases(notes, base_shift):
    """Choose whole-phrase octave shifts, never silently fold individual notes.

    A phrase boundary requires a rest of 0.35--1 seconds, depending on median
    note duration. A phrase wider than the instrument can still lose notes.
    The DP first minimizes weighted loss, then register changes and displacement.
    """
    threshold = max(.35, min(1, .75 * median(n['end'] - n['start'] for n in notes)))
    phrases = [[]]
    for n in notes:
        if phrases[-1] and n['start'] - phrases[-1][-1]['end'] >= threshold:
            phrases.append([])
        phrases[-1].append(n)
    weights = note_weights(notes)
    offset, history, previous = 0, [], {}
    for phrase in phrases:
        costs, links = {}, {}
        local_weights = weights[offset:offset + len(phrase)]
        offset += len(phrase)
        for shift in OCTAVE_SHIFTS:
            lost = sum(w for n, w in zip(phrase, local_weights)
                       if not MIN_PITCH <= n['pitch'] + base_shift + shift <= MAX_PITCH)
            displacement = abs(shift) / 12 * .2
            if previous:
                parent = min(previous, key=lambda p: (previous[p][0],
                             previous[p][1] + abs(shift - p) / 12, abs(p), p))
                costs[shift] = (previous[parent][0] + lost,
                                previous[parent][1] + abs(shift - parent) / 12 + displacement)
                links[shift] = parent
            else:
                costs[shift] = (lost, displacement)
        history.append(links)
        previous = costs
    shift = min(previous, key=lambda s: (*previous[s], abs(s), s))
    shifts = []
    for links in reversed(history):
        shifts.append(shift)
        shift = links.get(shift, 0)
    result, adjustments = [], []
    for phrase, shift in zip(phrases, reversed(shifts)):
        kept = [dict(n, pitch=n['pitch'] + base_shift + shift) for n in phrase
                if MIN_PITCH <= n['pitch'] + base_shift + shift <= MAX_PITCH]
        result.extend(kept)
        if shift and kept:
            adjustments.append(dict(start=phrase[0]['start'], end=phrase[-1]['end'],
                                    semitones=shift, notes=len(kept)))
    return result, adjustments


def prepare(parts, names, options):
    options.validate()
    ranked = rank_parts(parts, names)
    candidates = [k for k, _ in ranked if (options.track is None or k[0] == options.track)
                  and (options.channel is None or k[1] == options.channel)]
    if not candidates:
        raise ValueError('所选音轨或通道没有可用音符。')
    key = candidates[0]
    raw = parts[key]
    notes = simplify(raw, options.melody_mode, options.trim_silence)
    shifts = OCTAVE_SHIFTS if options.auto_octave else (0,)
    weights = note_weights(notes)
    octave = max(shifts, key=lambda s: (sum(w for n, w in zip(notes, weights)
                 if MIN_PITCH <= n['pitch']+options.transpose+s <= MAX_PITCH), -abs(s), -s))
    shift = options.transpose + octave
    adjustments = []
    playable = [dict(n, pitch=n['pitch']+shift)
                for n in notes if MIN_PITCH <= n['pitch']+shift <= MAX_PITCH]
    if options.phrase_octave and len(playable) < len(notes):
        playable, adjustments = fit_phrases(notes, shift)
    playable = [dict(n, start=n['start']/options.speed, end=n['end']/options.speed) for n in playable]
    adjustments = [dict(a, start=a['start']/options.speed, end=a['end']/options.speed) for a in adjustments]
    if not playable:
        raise ValueError('所有音符都超出口琴音域，请开启自动八度或调整移调。')
    if playable[-1]['end'] > MAX_SECONDS:
        raise ValueError('演奏超过 20 分钟，请先裁剪曲谱或提高速度。')
    report = dict(track=key[0], channel=key[1]+1, track_name=names.get(key[0], ''),
                  source_notes=len(raw), melody_notes=len(playable),
                  removed_polyphony=len(raw)-len(notes), dropped_out_of_range=len(notes)-len(playable),
                  transpose_semitones=shift, speed=options.speed,
                  octave_adjustments=adjustments, phrase_adjusted_notes=sum(a['notes'] for a in adjustments),
                  melody_mode=options.melody_mode)
    return playable, report
