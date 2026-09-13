import unittest
from harmonica_studio.transport import PlaybackClock, TimeMap, playback_anchors
from harmonica_studio.schedule import build_events
from harmonica_studio.preview import decode_events


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.now=0.0
        self.clock=PlaybackClock(now=lambda:self.now)

    def test_animation_advances_between_device_samples(self):
        self.clock.reset(3.0,True)
        positions=[]
        for index in range(1,7):
            self.now=index/60;positions.append(self.clock.position())
        self.assertAlmostEqual(positions[-1],3.1)
        self.assertTrue(all(a<b for a,b in zip(positions,positions[1:])))

    def test_small_device_correction_is_smooth_and_monotonic(self):
        self.clock.reset(0,True);self.now=.1
        before=self.clock.position();self.clock.synchronize(.08,True)
        self.assertAlmostEqual(self.clock.position(),before)
        self.now=.16;half=self.clock.position()
        self.now=.22;after=self.clock.position()
        self.assertTrue(before<half<after)
        self.assertAlmostEqual(after,.2)

    def test_seek_and_pause_take_effect_immediately(self):
        self.clock.reset(20,True);self.now=.1
        self.clock.synchronize(5,True,discontinuity=True)
        self.assertEqual(self.clock.position(),5)
        self.now=.15;self.clock.synchronize(5.05,False)
        self.now=10;self.assertEqual(self.clock.position(),5.05)

    def test_large_drift_resynchronizes_and_end_is_clamped(self):
        self.clock.reset(0,True);self.now=.1
        self.clock.synchronize(6,True)
        self.assertEqual(self.clock.position(),6)
        self.now=2;self.assertEqual(self.clock.position(duration=7),7)

    def test_cached_map_handles_lead_in_gaps_and_inverse(self):
        mapping=TimeMap([(0,.1),(1,1.1),(2,2.2),(3,3.2)])
        inverse=TimeMap([(b,a) for a,b in mapping.points])
        for position in (0,.5,1,1.5,2,2.7,4):
            self.assertAlmostEqual(inverse(mapping(position)),position)
        self.assertEqual(TimeMap()(-3),0)


class PlaybackAnchorsTests(unittest.TestCase):
    @staticmethod
    def make_maps(notes):
        actual = decode_events(build_events(notes)[0])
        anchors = playback_anchors(notes, actual)
        return actual, TimeMap(anchors), TimeMap((b, a) for a, b in anchors)

    @staticmethod
    def note(pitch, start, end):
        return dict(pitch=pitch, start=start, end=end, velocity=80)

    def test_legato_cursor_keeps_moving_across_key_release_gaps(self):
        for pitch, expected_gap in ((62, .020), (73, .045)):
            with self.subTest(next_pitch=pitch):
                notes = [self.note(60, 0, 1), self.note(pitch, 1, 2), self.note(64, 2, 3)]
                actual, forward, inverse = self.make_maps(notes)
                start, end = actual[0]['end'], actual[1]['start']
                self.assertAlmostEqual(end - start, expected_gap)
                # Sample animation frames inside the intentional physical key-up gap.
                frames = [start]
                while frames[-1] + 1/60 < end:
                    frames.append(frames[-1] + 1/60)
                frames.append(end)
                positions = [inverse(t) for t in frames]
                self.assertTrue(all(a < b for a, b in zip(positions, positions[1:])), positions)
                for audio, score in zip(frames, positions):
                    self.assertAlmostEqual(score, audio - .1)
                self.assertAlmostEqual(forward(1), actual[1]['start'])

    def test_tiny_score_rest_does_not_stretch_into_visible_slowdown(self):
        for pitch in (62, 73):
            with self.subTest(next_pitch=pitch):
                notes = [self.note(60, 0, 1), self.note(pitch, 1.001, 2)]
                actual, _, inverse = self.make_maps(notes)
                start, end = actual[0]['end'], actual[1]['start']
                for audio in (start, (start + end) / 2, end):
                    self.assertAlmostEqual(inverse(audio), audio - .1)

    def test_onsets_and_final_release_stay_synchronized(self):
        notes = [self.note(60, 0, 1), self.note(73, 1, 2), self.note(62, 2, 3)]
        actual, forward, inverse = self.make_maps(notes)
        for logical, physical in zip(notes, actual):
            self.assertAlmostEqual(forward(logical['start']), physical['start'])
            self.assertAlmostEqual(inverse(physical['start']), logical['start'])
        self.assertAlmostEqual(forward(notes[-1]['end']), actual[-1]['end'])
        self.assertAlmostEqual(inverse(actual[-1]['end']), notes[-1]['end'])

    def test_leading_silence_and_real_rests_keep_their_timing(self):
        notes = [self.note(60, 1.25, 1.5), self.note(62, 2.5, 2.8), self.note(73, 4, 4.7)]
        _, forward, inverse = self.make_maps(notes)
        positions = (0, .25, 1.25, 1.5, 1.8, 2.5, 2.8, 3.2, 4, 4.7)
        physical = [forward(t) for t in positions]
        self.assertTrue(all(a < b for a, b in zip(physical, physical[1:])))
        for score, audio in zip(positions, physical):
            self.assertAlmostEqual(audio, score + .1)
            self.assertAlmostEqual(inverse(audio), score)
        self.assertEqual(inverse(0), 0)

    def test_dense_delayed_notes_remain_invertible_and_advance(self):
        notes = [self.note(61, 0, .01), self.note(73, .01, .02), self.note(60, .03, .04)]
        self.assertGreater(build_events(notes)[1], 0)
        actual, forward, inverse = self.make_maps(notes)
        first, last = actual[0]['start'], actual[-1]['end']
        frames = [first + (last - first) * i / 100 for i in range(101)]
        positions = [inverse(t) for t in frames]
        self.assertTrue(all(a < b for a, b in zip(positions, positions[1:])), positions)
        for audio, score in zip(frames, positions):
            self.assertAlmostEqual(forward(score), audio)
        for logical, physical in zip(notes, actual):
            self.assertAlmostEqual(forward(logical['start']), physical['start'])
            self.assertAlmostEqual(inverse(physical['start']), logical['start'])
        self.assertAlmostEqual(positions[-1], notes[-1]['end'])

    def test_empty_score_has_identity_time_mapping(self):
        anchors = playback_anchors([], [])
        self.assertEqual(anchors, [])
        mapping = TimeMap(anchors)
        self.assertEqual(mapping(-1), 0)
        self.assertEqual(mapping(0), 0)
        self.assertEqual(mapping(2.5), 2.5)
