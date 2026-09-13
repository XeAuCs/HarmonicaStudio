from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Options:
    speed: float = 1.0
    transpose: int = 0
    auto_octave: bool = True
    trim_silence: bool = True
    melody_mode: str = 'sustain'
    track: int | None = None
    channel: int | None = None  # Zero-based internally.
    skip_long_rests: bool = False
    phrase_octave: bool = False

    def validate(self):
        if type(self.speed) not in (int,float) or not math.isfinite(self.speed) or not .25 <= self.speed <= 2:
            raise ValueError('速度必须在 0.25 至 2 倍之间。')
        if type(self.transpose) is not int or not -24 <= self.transpose <= 24:
            raise ValueError('移调必须是 -24 至 24 之间的整数。')
        if self.melody_mode not in ('highest', 'sustain', 'continuous'):
            raise ValueError('未知旋律提取模式。')
        if any(type(value) is not bool for value in (self.auto_octave, self.trim_silence, self.skip_long_rests, self.phrase_octave)):
            raise ValueError('八度和空白设置必须是布尔值。')
        if self.track is not None and (type(self.track) is not int or not 0 <= self.track < 1024):
            raise ValueError('音轨编号无效。')
        if self.channel is not None and (type(self.channel) is not int or not 0 <= self.channel < 16):
            raise ValueError('通道编号无效。')
        return self
