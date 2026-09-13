"""Pitch mapping and physical-time input scheduling."""
KEYS = ['SC02C', 'SC02C', 'SC02D', 'SC02D', 'SC02E', 'SC02F',
        'SC02F', 'SC030', 'SC030', 'SC031', 'SC031', 'SC032']
SHARP = {1, 3, 6, 8, 10}


def mapping(pitch):
    if not 48 <= pitch <= 85:
        raise ValueError('音高超出 C3 至 C6# 的映射范围。')
    pc = pitch % 12
    modifiers = []
    if pitch < 60:
        modifiers.append('LButton')
    elif pitch >= 72:
        modifiers.append('RButton')
    if pc in SHARP:
        modifiers.append('MButton')
    key = 'SC033' if pitch >= 84 else KEYS[pc]
    return key, modifiers


def build_events(notes):
    events, released, delayed = [], 0, 0
    for idx, note in enumerate(notes):
        key, mods = mapping(note['pitch'])
        desired = 100 + round(note['start'] * 1000)
        onset = max(desired, released + 20 + (25 if mods else 0))
        if onset > desired:
            delayed += 1
        intended_end = 100 + round(note['end'] * 1000)
        if idx + 1 < len(notes):
            following = notes[idx + 1]
            _, next_mods = mapping(following['pitch'])
            next_onset = 100 + round(following['start'] * 1000)
            intended_end = min(intended_end, next_onset - 20 - (25 if next_mods else 0))
        end = max(onset + 25, intended_end)
        if end > 1_200_000:
            raise ValueError('按键编排后超过 20 分钟，请减少音符或裁剪曲谱。')
        for mod in mods:
            events.append([onset-25, mod, 1])
        events.append([onset, key, 1])
        events.append([end, key, 0])
        for mod in mods:
            events.append([end, mod, 0])
        released = end
    return sorted(events, key=lambda e: e[0]), delayed


