"""Actual Windows MCI checks using silent PCM, never user music or game input."""
from pathlib import Path
import sys
import tempfile
import time
import unittest
import wave

from harmonica_studio.playback import AudioPlayer


@unittest.skipUnless(sys.platform == 'win32', 'Requires native Windows MCI')
class NativeAudioTests(unittest.TestCase):
    def test_long_unicode_export_path_loads_seeks_and_releases_private_copy(self):
        with tempfile.TemporaryDirectory(prefix='harmonica-native-') as temporary:
            folder = Path(temporary) / ('nested-export-' + 'x' * 65)
            folder.mkdir()
            path = folder / 'Bad Apple!!（坏家伙）- 东方Project (Remix Version)-试听.wav'
            self.assertGreater(len(str(path)), 128)
            with wave.open(str(path), 'wb') as wav:
                wav.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
                wav.writeframes(b'\0\0' * 22050)
            original = path.read_bytes()
            player = AudioPlayer()
            staged = None
            try:
                player.load(path)
                self.assertEqual(player.path, path.resolve())
                self.assertAlmostEqual(player.duration, 1, places=2)
                staged = Path(player._staged_audio.name) if player._staged_audio else None
                player.seek(.25)
                self.assertAlmostEqual(player.position, .25, places=2)
                player.play()
                deadline = time.monotonic() + 2
                while player.position <= .25 and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertGreater(player.position, .25)
                player.pause()
                self.assertFalse(player.playing)
                player.stop()
                self.assertEqual(player.position, 0)
            finally:
                player.close()
            if staged is not None:
                self.assertFalse(staged.exists())
            self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
