"""Bounded SMF 0/1 and RMID parser; tempo map and MIDI export."""
from pathlib import Path
from collections import defaultdict, deque
import bisect, struct

def read_midi(path):
    if Path(path).stat().st_size > 20 * 1024 * 1024:
        raise ValueError("文件超过 20 MB，请先裁剪或分轨。")
    data = Path(path).read_bytes()
    if data[:4] == b'RIFF' and data[8:12] == b'RMID':
        pos = 12
        while pos + 8 <= len(data):
            tag, size = data[pos:pos+4], int.from_bytes(data[pos+4:pos+8], 'little')
            if tag == b'data':
                data = data[pos+8:pos+8+size]
                break
            pos += 8 + size + size % 2
    if data[:4] != b'MThd' or len(data) < 14:
        raise ValueError('不是有效 MIDI 文件，不能把网页或音频改后缀当作 MIDI。')
    hlen = int.from_bytes(data[4:8], 'big')
    if hlen < 6 or 8 + hlen > len(data):
        raise ValueError('MIDI 文件头长度无效。')
    fmt, count, ppq = struct.unpack('>HHH', data[8:14])
    if fmt not in (0, 1) or not ppq or ppq & 0x8000:
        raise ValueError('目前支持标准 MIDI 格式 0/1、每拍刻度计时。')
    if count < 1 or count > 1024 or (fmt == 0 and count != 1):
        raise ValueError('MIDI 音轨数量无效。')
    pos, raw, tempos, names = 8 + hlen, [], [(0, 500000)], {}
    for track in range(count):
        if pos + 8 > len(data) or data[pos:pos+4] != b'MTrk':
            raise ValueError('MIDI 音轨头缺失或文件不完整。')
        size = int.from_bytes(data[pos+4:pos+8], 'big')
        buf = data[pos+8:pos+8+size]
        if len(buf) != size:
            raise ValueError('MIDI 音轨被截断。')
        pos += 8 + size
        i, tick, running = 0, 0, None
        active = defaultdict(deque)
        def byte():
            nonlocal i
            if i >= len(buf):
                raise ValueError('MIDI 事件被截断。')
            n = buf[i]
            i += 1
            return n
        def vlq():
            n = 0
            for _ in range(4):
                b = byte()
                n = (n << 7) | (b & 127)
                if not b & 128:
                    return n
            raise ValueError('MIDI 可变长度数值无效。')
        def block(n):
            return bytes(byte() for _ in range(n))
        while i < len(buf):
            tick += vlq()
            status = byte()
            if status < 128:
                if running is None:
                    raise ValueError('MIDI running status 无效。')
                i -= 1
                status = running
            if status == 255:
                kind = byte()
                value = block(vlq())
                running = None
                if kind == 81 and len(value) == 3:
                    tempo = int.from_bytes(value, 'big')
                    if tempo <= 0:
                        raise ValueError('MIDI 速度无效。')
                    tempos.append((tick, tempo))
                elif kind == 3:
                    names[track] = value.decode('utf-8', errors='replace')
                elif kind == 47:
                    break
            elif status in (240, 247):
                block(vlq())
                running = None
            elif 128 <= status <= 239:
                running = status
                kind, channel = status >> 4, status & 15
                a = byte()
                b = byte() if kind not in (12, 13) else 0
                if a >= 128 or b >= 128:
                    raise ValueError('MIDI 通道数据无效。')
                key = channel, a
                if kind == 9 and b:
                    active[key].append((tick, b))
                elif kind == 8 or (kind == 9 and not b):
                    if active[key]:
                        start, velocity = active[key].popleft()
                        if tick > start:
                            if len(raw) >= 100000:
                                raise ValueError('音符过多，请先拆分曲谱。')
                            raw.append((track, channel, a, start, tick, velocity))
            else:
                raise ValueError('不支持的 MIDI 系统事件。')
        if any(active.values()):
            raise ValueError('MIDI 存在未结束的音符，请先用打谱软件修复。')
    # Tempo map uses all tracks; the last event at an identical tick wins.
    tempo_by_tick = dict(tempos)
    ticks = sorted(tempo_by_tick)
    seconds, elapsed = [], 0.0
    for j, t in enumerate(ticks):
        if j:
            elapsed += (t - ticks[j-1]) * tempo_by_tick[ticks[j-1]] / ppq / 1e6
        seconds.append(elapsed)
    def to_sec(t):
        j = bisect.bisect_right(ticks, t) - 1
        return seconds[j] + (t-ticks[j]) * tempo_by_tick[ticks[j]] / ppq / 1e6
    parts = defaultdict(list)
    for track, ch, pitch, start, end, vel in raw:
        if ch != 9:
            parts[(track, ch)].append(dict(pitch=pitch, start=to_sec(start), end=to_sec(end), velocity=vel))
    if not parts:
        raise ValueError('文件没有可用的非打击乐音符。')
    for notes in parts.values():
        notes.sort(key=lambda n: (n['start'], n['pitch']))
    return parts, names


def vlq_out(n):
    result = [n & 127]
    while n >> 7:
        n >>= 7
        result.insert(0, (n & 127) | 128)
    return bytes(result)


def write_midi(notes, path):
    # 1000 ticks/second: 500000 us/quarter, 500 ticks/quarter.
    evs = []
    for n in notes:
        start, end = round(n['start']*1000), round(n['end']*1000)
        evs.extend([(start, 1, bytes([144, n['pitch'], n['velocity']])),
                    (max(start+1, end), 0, bytes([128, n['pitch'], 0]))])
    body, last = bytearray(b'\x00\xff\x51\x03\x07\xa1\x20'), 0
    for tick, _, payload in sorted(evs):
        body.extend(vlq_out(tick-last) + payload)
        last = tick
    body.extend(b'\x00\xff\x2f\x00')
    Path(path).write_bytes(b'MThd'+struct.pack('>IHHH',6,0,1,500)+b'MTrk'+struct.pack('>I',len(body))+body)


