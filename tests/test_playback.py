from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import wave

from harmonica_studio.playback import AudioPlayer


class FakeMCI:
    """Small deterministic audio device with an independently advanced clock."""

    def __init__(self):
        self.commands = []
        self.devices = {}
        self.failure = None

    def __call__(self, command):
        self.commands.append(command)
        if self.failure and command.startswith(self.failure):
            raise RuntimeError('测试音频设备错误')
        opening = re.fullmatch(r'open "(.+)" type waveaudio alias (\w+)', command)
        if opening:
            path, alias = opening.groups()
            if alias in self.devices:
                raise AssertionError('alias already open')
            with wave.open(path, 'rb') as wav:
                length = round(wav.getnframes() / wav.getframerate() * 1000)
            self.devices[alias] = dict(position=0, length=length, mode='stopped')
            return ''
        action, alias, *parameters = command.split()
        state = self.devices[alias]
        if action == 'set':
            assert parameters == ['time', 'format', 'milliseconds']
        elif action == 'status':
            return str(state[parameters[0]])
        elif action == 'seek':
            assert parameters[0] == 'to'
            target = parameters[1]
            position = {'start': 0, 'end': state['length']}.get(target)
            if position is None:
                position = int(target)
            assert 0 <= position <= state['length']
            state.update(position=position, mode='stopped')
        elif action == 'play':
            if state['position'] >= state['length']:
                raise RuntimeError('cannot play beyond end')
            state['mode'] = 'playing'
        elif action == 'pause':
            assert state['mode'] == 'playing'
            state['mode'] = 'paused'
        elif action == 'close':
            del self.devices[alias]
        else:
            raise AssertionError(command)
        return ''

    def advance(self, milliseconds):
        for state in self.devices.values():
            if state['mode'] == 'playing':
                state['position'] = min(state['length'], state['position'] + milliseconds)
                if state['position'] == state['length']:
                    state['mode'] = 'stopped'


class AudioPlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='harmonica-audio-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / '口琴 试听.wav'
        with wave.open(str(self.path), 'wb') as wav:
            wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0' * (8000 * 2 * 2))
        self.backend = FakeMCI()
        self.player = AudioPlayer(sender=self.backend)

    def test_unloaded_player_is_lazy_and_stop_close_are_safe(self):
        with patch('harmonica_studio.playback.sys.platform', 'linux'):
            player = AudioPlayer()
            self.assertEqual(player.duration, 0)
            self.assertEqual(player.position, 0)
            self.assertFalse(player.playing)
            player.pause()
            player.stop()
            player.close()
        with self.assertRaisesRegex(RuntimeError, '请先'):
            self.player.play()
        with self.assertRaisesRegex(RuntimeError, '请先'):
            self.player.seek(1)
        self.assertFalse(self.backend.commands)

    def test_load_uses_unicode_path_and_native_duration(self):
        self.player.load(self.path)
        self.assertEqual(self.player.path, self.path.resolve())
        self.assertEqual(self.player.duration, 2)
        self.assertEqual(self.player.position, 0)
        self.assertFalse(self.player.playing)
        self.assertIn(f'"{self.path.resolve()}"', self.backend.commands[0])

    def test_pause_resume_preserves_real_position(self):
        self.player.load(self.path)
        self.player.play(.375)
        self.backend.advance(425)
        self.assertTrue(self.player.playing)
        self.assertAlmostEqual(self.player.position, .8)
        self.player.pause()
        self.backend.advance(1000)
        self.assertFalse(self.player.playing)
        self.assertAlmostEqual(self.player.position, .8)
        self.player.play()
        self.backend.advance(200)
        self.assertTrue(self.player.playing)
        self.assertEqual(self.player.position, 1)

    def test_seek_can_stop_or_continue_and_stop_resets(self):
        self.player.load(self.path)
        self.player.play()
        self.player.seek(.75)
        self.backend.advance(500)
        self.assertEqual(self.player.position, .75)
        self.assertFalse(self.player.playing)
        self.player.seek(1.25, resume=True)
        self.backend.advance(250)
        self.assertEqual(self.player.position, 1.5)
        self.assertTrue(self.player.playing)
        self.player.stop()
        self.assertEqual(self.player.position, 0)
        self.assertFalse(self.player.playing)

    def test_bounds_end_and_restart(self):
        self.player.load(self.path)
        self.player.play(-100)
        self.assertTrue(self.player.playing)
        self.assertEqual(self.player.position, 0)
        self.player.seek(1e308, resume=True)
        self.assertEqual(self.player.position, 2)
        self.assertFalse(self.player.playing)
        self.player.play()
        self.assertFalse(self.player.playing)
        self.player.play(0)
        self.backend.advance(3000)
        self.assertEqual(self.player.position, 2)
        self.assertFalse(self.player.playing)

    def test_invalid_positions_do_not_change_playback(self):
        self.player.load(self.path)
        self.player.play(.5)
        count = len(self.backend.commands)
        for value in (float('inf'), float('-inf'), float('nan'), None, 'invalid'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.player.seek(value)
        self.assertEqual(len(self.backend.commands), count)
        self.assertTrue(self.player.playing)
        self.assertEqual(self.player.position, .5)

    def test_reload_and_two_players_own_independent_aliases(self):
        self.player.load(self.path)
        other = AudioPlayer(sender=self.backend)
        other.load(self.path)
        self.assertEqual(len(self.backend.devices), 2)
        self.player.play(.5)
        other.play(1)
        self.player.load(self.path)
        self.assertEqual(len(self.backend.devices), 2)
        self.assertEqual(self.player.position, 0)
        self.assertEqual(other.position, 1)
        self.player.close()
        self.assertEqual(len(self.backend.devices), 1)
        self.assertEqual(self.player.duration, 0)
        self.assertEqual(self.player.position, 0)
        self.assertIsNone(self.player.path)
        other.close()
        self.assertFalse(self.backend.devices)

    def test_invalid_paths_and_non_wav_never_reach_mci(self):
        invalid = Path(self.temp.name) / 'invalid.wav'
        invalid.write_text('not a WAV')
        for path in (invalid, invalid.parent / 'missing.wav', 'file" close all', 'x\ny.wav', 'x\0y.wav'):
            with self.subTest(path=str(path)), self.assertRaises(RuntimeError):
                self.player.load(path)
        self.assertFalse(self.backend.commands)

    def test_failed_initialization_releases_device(self):
        self.backend.failure = 'set '
        with self.assertRaisesRegex(RuntimeError, '测试音频设备错误'):
            self.player.load(self.path)
        self.assertFalse(self.backend.devices)
        self.assertFalse(self.player.playing)
        self.assertEqual(self.player.duration, 0)
        self.backend.failure = None
        self.player.load(self.path)
        self.assertEqual(self.player.duration, 2)

    def test_native_error_is_readable(self):
        class FailingWinMM:
            def mciSendStringW(self, command, buffer, length, window):
                return 281

            def mciGetErrorStringW(self, code, buffer, length):
                buffer.value = '音频设备不可用'
                return True

        player = AudioPlayer()
        player._winmm = FailingWinMM()
        with self.assertRaisesRegex(RuntimeError, '音频设备不可用.*281'):
            player._send('status harmless mode')


if __name__ == '__main__':
    unittest.main()
