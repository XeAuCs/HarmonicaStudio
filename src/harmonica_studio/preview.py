"""Render exactly the exported input timeline into a local synthesized WAV."""
from array import array
import math, sys, wave

KEY_PITCH = dict(zip(('SC02C','SC02D','SC02E','SC02F','SC030','SC031','SC032','SC033'),(0,2,4,5,7,9,11,12)))


def decode_events(events):
    held, playing, notes = set(), {}, []
    last = -1
    for ms, key, down in events:
        if ms < last or ms < 0 or key not in {*KEY_PITCH, 'LButton', 'RButton', 'MButton'} or down not in (0,1):
            raise ValueError('按键时间表无效。')
        last = ms
        if down:
            if key in held:
                raise ValueError('发现重复按下事件。')
            if key in KEY_PITCH:
                if playing:
                    raise ValueError('发现重叠发声音符。')
                pitch = 60 + KEY_PITCH[key] + 12*('RButton' in held) - 12*('LButton' in held) + ('MButton' in held)
                playing[key] = (ms, pitch)
            held.add(key)
            if 'LButton' in held and 'RButton' in held:
                raise ValueError('高低八度修饰键冲突。')
        else:
            if key not in held:
                raise ValueError('发现不配对的松开事件。')
            if key in playing:
                start, pitch = playing.pop(key)
                if ms <= start:
                    raise ValueError('发声时长无效。')
                notes.append(dict(pitch=pitch, start=start/1000, end=ms/1000, velocity=80))
            held.remove(key)
    if held or playing:
        raise ValueError('播放结束后仍有按键按住。')
    return notes


def render_wav(events, path, cancel=None, sample_rate=22050):
    notes = decode_events(events)
    if not notes:
        raise ValueError('没有可试听的音符。')
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        position = 0
        for n in notes:
            if cancel and cancel.is_set():
                raise InterruptedError('转换已取消。')
            start, end = round(n['start']*sample_rate), round(n['end']*sample_rate)
            if start > position:
                # Chunk silence to bound allocation on long rests.
                gap = start-position
                while gap:
                    if cancel and cancel.is_set():
                        raise InterruptedError('转换已取消。')
                    size = min(gap, sample_rate)
                    out.writeframesraw(b'\0\0'*size)
                    gap -= size
            count = max(1,end-start)
            f = 440*2**((n['pitch']-69)/12)
            for offset in range(0,count,sample_rate):
                if cancel and cancel.is_set():
                    raise InterruptedError('转换已取消。')
                chunk = array('h')
                for i in range(offset, min(count,offset+sample_rate)):
                    t=i/sample_rate
                    phase=2*math.pi*f*t
                    env=min(1,t/.012)*min(1,(count-i)/sample_rate/.025)
                    tone=math.sin(phase)+.28*math.sin(2*phase)+.13*math.sin(3*phase)
                    chunk.append(round(32767*.17*tone*env))
                if sys.byteorder != 'little':
                    chunk.byteswap()
                out.writeframesraw(chunk.tobytes())
            position=end
        out.writeframesraw(b'\0\0'*(sample_rate//3))
