import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
import wave
from harmonica_studio.midi import read_midi,write_midi,vlq_out
from harmonica_studio.melody import simplify,prepare
from harmonica_studio.models import Options
from harmonica_studio.schedule import build_events,mapping
from harmonica_studio.preview import decode_events
from harmonica_studio.service import convert
from harmonica_studio.storage import load_options,save_options

SAMPLE=Path(__file__).resolve().parents[1]/'samples/欢乐颂.mid'
def note(p=60,s=0,e=.5):return dict(pitch=p,start=s,end=e,velocity=80)
def smf(*tracks,fmt=1):
    return b'MThd'+struct.pack('>IHHH',6,fmt,len(tracks),480)+b''.join(b'MTrk'+struct.pack('>I',len(t))+t for t in tracks)
EOT=b'\0\xff\x2f\0'

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='harmonica-test-');self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def parse(self,data):
        path=self.root/'test.mid';path.write_bytes(data);return read_midi(path)
    def test_tempo_map_in_other_track(self):
        tempos=b'\0\xff\x51\3\x07\xa1\x20'+vlq_out(480)+b'\xff\x51\3\x0f\x42\x40'+EOT
        notes=b'\0\x90\x3c\x50'+vlq_out(960)+b'\x80\x3c\0'+EOT
        parts,_=self.parse(smf(tempos,notes));self.assertAlmostEqual(parts[(1,0)][0]['end'],1.5)
    def test_running_status_and_velocity_zero(self):
        track=b'\0\x90\x3c\x50'+vlq_out(240)+b'\x3c\0\0\x3e\x50'+vlq_out(240)+b'\x3e\0'+EOT
        parts,_=self.parse(smf(track,fmt=0));self.assertEqual([n['pitch'] for n in parts[(0,0)]],[60,62])
    def test_rmid(self):
        write_midi([note()],self.root/'raw.mid');raw=(self.root/'raw.mid').read_bytes()
        data=b'RMIDdata'+struct.pack('<I',len(raw))+raw
        parts,_=self.parse(b'RIFF'+struct.pack('<I',len(data))+data);self.assertEqual(parts[(0,0)][0]['pitch'],60)
    def test_invalid_and_truncated(self):
        for raw in (b'<html>',smf(b'\0\x90\x3c'),b'MThd'+struct.pack('>IHHH',999,0,1,480),smf(EOT,fmt=2)):
            with self.subTest(raw=raw),self.assertRaises(ValueError):self.parse(raw)
    def test_unclosed_note_rejected(self):
        with self.assertRaises(ValueError):self.parse(smf(b'\0\x90\x3c\x50'+EOT,fmt=0))
    def test_percussion_excluded(self):
        track=b'\0\x99\x3c\x50'+vlq_out(480)+b'\x89\x3c\0'+EOT
        with self.assertRaises(ValueError):self.parse(smf(track,fmt=0))
    def test_highest_groups_staggered_chord_without_mutation(self):
        source=[note(60),note(64,.01,.5),note(67,.02,.5)];before=json.dumps(source)
        got=simplify(source,'highest');self.assertEqual([n['pitch'] for n in got],[67]);self.assertEqual(got[0]['start'],0)
        self.assertEqual(json.dumps(source),before)
    def test_sustain_suppresses_low_accompaniment(self):
        source=[note(72,0,1),note(48,.25,.5),note(74,1,1.5)]
        self.assertEqual([n['pitch'] for n in simplify(source)],[72,74])
        self.assertEqual(len(simplify(source,'highest')),3)
    def test_overlap_trimmed_and_silence_optional(self):
        got=simplify([note(60,2,4),note(62,3,5)],trim=False)
        self.assertEqual((got[0]['start'],got[0]['end']),(2,3));self.assertEqual(simplify([]),[])
    def test_options_validation(self):
        for kw in ({'speed':0},{'speed':float('nan')},{'speed':'1'},{'transpose':.5},{'auto_octave':'yes'},{'track':-1},{'channel':16}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):Options(**kw).validate()
    def test_sample_selects_real_melody(self):
        parts,names=read_midi(SAMPLE);got,report=prepare(parts,names,Options())
        self.assertEqual(report['melody_notes'],94);self.assertEqual(report['track_name'],'Bassoon')
        self.assertEqual(report['transpose_semitones'],12);self.assertEqual([n['pitch'] for n in got[:4]],[66,66,67,69])
    def test_explicit_channel_selection(self):
        parts={(0,0):[note(60)],(0,1):[note(64)]}
        got,_=prepare(parts,{},Options(channel=1));self.assertEqual(got[0]['pitch'],64)
        with self.assertRaises(ValueError):prepare(parts,{},Options(channel=2))
    def test_all_playable_pitches_roundtrip(self):
        notes=[note(p,i*.5,i*.5+.3) for i,p in enumerate(range(48,86))]
        events,delayed=build_events(notes);decoded=decode_events(events)
        self.assertEqual([n['pitch'] for n in decoded],list(range(48,86)));self.assertEqual(delayed,0)
        for p in (47,86):
            with self.assertRaises(ValueError):mapping(p)
    def test_fast_notes_remain_monophonic_and_balanced(self):
        events,delayed=build_events([note(p,i*.01,i*.01+.01) for i,p in enumerate([48,85,61,73,60])])
        notes=decode_events(events);self.assertGreater(delayed,0)
        self.assertTrue(all(a['end']<b['start'] for a,b in zip(notes,notes[1:])))
    def test_invalid_schedule_rejected(self):
        for events in ([[0,'SC02C',1]],[[1,'SC02C',0]],[[0,'SC02C',1],[1,'SC02D',1]],[[0,'LButton',1],[0,'RButton',1]]):
            with self.subTest(events=events),self.assertRaises(ValueError):decode_events(events)
    def test_physical_duration_limit(self):
        with self.assertRaises(ValueError):build_events([note(60,1200,1201)])
    def test_export_roundtrip_and_unique_output(self):
        source=self.root/'input.mid';write_midi([note(60),note(73,.6,1)],source)
        folder,report=convert(source,self.root/'exports');folder2,_=convert(source,self.root/'exports')
        self.assertNotEqual(folder,folder2);self.assertEqual(len(list(folder.iterdir())),7)
        actual=json.loads((folder/'音符.json').read_text('utf-8'));parts,_=read_midi(folder/'口琴单旋律.mid')
        self.assertEqual(parts[(0,0)],actual)
        with wave.open(str(folder/'试听.wav')) as wav:
            self.assertEqual(wav.getnchannels(),1);self.assertAlmostEqual(wav.getnframes()/wav.getframerate(),report['duration_seconds']+1/3,places=3)
        self.assertNotIn('__EVENTS__',(folder/'演奏脚本.ahk').read_text('utf-8-sig'))
    def test_cancel_does_not_leave_partial_export(self):
        event=threading.Event();event.set()
        with self.assertRaises(InterruptedError):convert(SAMPLE,self.root/'exports',cancel=event)
        self.assertEqual(list((self.root/'exports').iterdir()),[])
    def test_settings_recover_and_persist(self):
        path=self.root/'settings.json'
        for content in ('{bad','[]','null','{"auto_octave":"true"}'):
            path.write_text(content);self.assertEqual(load_options(path),Options())
        save_options(path,Options(speed=.75,transpose=2,track=1,channel=1))
        self.assertEqual(load_options(path),Options(speed=.75,transpose=2))

if __name__=='__main__':unittest.main()
