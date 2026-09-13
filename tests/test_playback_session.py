"""Transport regressions with shared silent backends and a real temporary WAV."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from harmonica_studio.app_state import AppState, Transport
from harmonica_studio.diagnostic_backends import SilentScriptPlayer
from harmonica_studio.diagnostics import FakeAudio
from harmonica_studio.playback_session import PlaybackSession
from harmonica_studio.project import make_project
from harmonica_studio.transport import PlaybackClock, TimeMap


class PlaybackSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='harmonica-playback-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with wave.open(str(self.root / '试听.wav'), 'wb') as sound:
            sound.setparams((1, 2, 22050, 0, 'NONE', 'not compressed'))
            sound.writeframes(b'\0\0' * 22050 * 3)
        self.state = AppState()
        self.state.set_project(make_project([
            dict(pitch=60, start=0, end=1, velocity=80),
            dict(pitch=62, start=10, end=11, velocity=80),
        ]), saved=True, new_document=True)
        self.events = []
        self.now = 0
        self.audio, self.player = FakeAudio(), SilentScriptPlayer()
        self.session = PlaybackSession(self.state, lambda event: self.events.append(event),
                                       self.root / 'control', audio=self.audio, player=self.player,
                                       clock=PlaybackClock(now=lambda: self.now))
        self.addCleanup(self.session.close)
        self.anchors = [(0, .1), (1, 1.1), (10, 1.7), (11, 2.7)]
        self.session.set_time_anchors(self.anchors)

    def test_compressed_rest_seek_pause_resume_and_end_preserve_document(self):
        s, session = self.state, self.session
        original = deepcopy(s)
        session.seek_score(10)
        self.assertEqual(s.position, 10)
        session.listen(self.root)
        self.assertAlmostEqual(self.audio.position, 1.7)
        self.now = .05
        self.assertAlmostEqual(session.clock.position(), 1.75)
        self.audio.advance(.1)
        session.pause()
        self.assertEqual(s.transport, Transport.PAUSED)
        self.assertAlmostEqual(s.logical_seek, 10.1)
        self.now = 5
        self.assertAlmostEqual(session.clock.position(), 1.8)
        session.listen(self.root)
        self.assertAlmostEqual(self.audio.position, 1.8)
        self.audio.advance(10)
        session.update_playback()
        self.assertEqual(s.transport, Transport.ENDED)
        self.assertEqual(s.position, self.audio.duration)
        session.listen(self.root)
        self.assertEqual(self.audio.position, 0)
        session.stop_preview()
        self.assertFalse(s.show_cursor)
        self.assertEqual(s.logical_seek, 0)
        for name in ('project', 'document_id', 'revision', 'saved_revision',
                     'autosave_revision', 'export_revision', 'result', 'score_duration'):
            self.assertEqual(getattr(s, name), getattr(original, name), name)
        self.assertIn('reset_timeline', self.events)

    def test_prepared_maps_are_adopted_without_main_thread_rebuilding(self):
        forward = TimeMap(self.anchors)
        inverse = TimeMap((b, a) for a, b in self.anchors)
        with patch('harmonica_studio.playback_session.TimeMap', side_effect=AssertionError('rebuild')):
            self.session.install_maps(self.anchors, forward, inverse)
        self.assertIs(self.session.to_audio, forward)
        self.assertIs(self.session.to_score, inverse)
        self.session.seek_score(10)
        self.session.load_preview(self.root)
        self.assertAlmostEqual(self.audio.position, 1.7)

    def test_edit_audio_close_failure_still_invalidates_transport_and_stops_game(self):
        session = self.session
        session.listen(self.root)
        self.player.play(self.root / '演奏脚本.ahk')
        self.audio.fail_next_close = True
        with self.assertLogs(level='ERROR'):
            session.invalidate_after_edit()
        self.assertEqual(self.state.preview_duration, 0)
        self.assertEqual(self.state.transport, Transport.READY)
        self.assertEqual(session.time_anchors, [])
        self.assertEqual(session.to_audio(10), 10)
        self.assertEqual(session.clock.position(), 0)
        self.assertEqual(self.state.position_label, '待生成')
        self.assertFalse(self.player.alive)

    def test_reset_close_failure_does_not_leave_previous_maps_usable(self):
        self.session.load_preview(self.root)
        self.audio.fail_next_close = True
        with self.assertRaises(RuntimeError):
            self.session.reset_preview()
        self.assertEqual(self.state.preview_duration, 0)
        self.assertEqual(self.session.time_anchors, [])
        self.assertEqual(self.session.to_score(2), 2)

    def test_shutdown_audio_failure_still_stops_game_and_can_retry_cleanup(self):
        self.session.load_preview(self.root)
        self.session.start_game(self.root)
        self.audio.fail_next_close = True
        with self.assertLogs(level='ERROR'):
            self.session.close()
        self.assertFalse(self.player.alive)
        self.session.close()
        self.assertIsNone(self.audio.path)


if __name__ == '__main__':
    unittest.main()
