"""Transparent melody heuristics; no claim of perfect voice separation."""


def simplify(notes, mode='sustain', trim=True):
    ordered = sorted(notes, key=lambda n: (n['start'], n['pitch']))
    if not ordered:
        return []
    selected, i = [], 0
    while i < len(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j]['start'] - ordered[i]['start'] <= .025:
            j += 1
        chosen = dict(max(ordered[i:j], key=lambda n: (n['pitch'], n['velocity'])))
        if mode == 'sustain' and selected:
            prev = selected[-1]
            # Discard a low accompaniment attack entirely contained under a sustained melody.
            if (prev['pitch'] - chosen['pitch'] >= 7 and
                    chosen['start'] < prev['end'] - .025 and chosen['end'] <= prev['end'] + .025):
                i = j
                continue
        selected.append(chosen)
        i = j
    origin = selected[0]['start'] if trim else 0
    result = []
    for i, n in enumerate(selected):
        end = min(n['end'], selected[i+1]['start']) if i+1 < len(selected) else n['end']
        if end > n['start']:
            result.append(dict(n, start=n['start']-origin, end=end-origin))
    return result


def rank_parts(parts, names):
    ranked = []
    for key, notes in parts.items():
        label = names.get(key[0], '').lower()
        named = any(s in label for s in ('melody', 'vocal', 'lead', '主旋律', '人声'))
        mono = len(simplify(notes, mode='highest')) / len(notes)
        reachable = max(sum(48 <= n['pitch']+s <= 85 for n in notes) for s in (0,-12,12,-24,24)) / len(notes)
        score = 4*named + 2*mono + reachable + min(len(notes)/100, 1)
        ranked.append((key, score))
    return sorted(ranked, key=lambda x: (-x[1], x[0]))


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
    shifts = (0,-12,12,-24,24,-36,36,-48,48,-60,60) if options.auto_octave else (0,)
    octave = max(shifts, key=lambda s: (sum(48 <= n['pitch']+options.transpose+s <= 85 for n in notes), -abs(s)))
    shift = options.transpose + octave
    playable = [dict(n, pitch=n['pitch']+shift, start=n['start']/options.speed, end=n['end']/options.speed)
                for n in notes if 48 <= n['pitch']+shift <= 85]
    if not playable:
        raise ValueError('所有音符都超出口琴音域，请开启自动八度或调整移调。')
    if playable[-1]['end'] > 1200:
        raise ValueError('演奏超过 20 分钟，请先裁剪曲谱或提高速度。')
    report = dict(track=key[0], channel=key[1]+1, track_name=names.get(key[0], ''),
                  source_notes=len(raw), melody_notes=len(playable),
                  removed_polyphony=len(raw)-len(notes), dropped_out_of_range=len(notes)-len(playable),
                  transpose_semitones=shift, speed=options.speed)
    return playable, report
