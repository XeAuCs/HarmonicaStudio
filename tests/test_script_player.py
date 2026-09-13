"""File protocol and lifecycle tests; no test here sends native keyboard input."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harmonica_studio.playback import ScriptPlayer


class FakeProcess:
    def __init__(self):
        self.returncode=None

    def poll(self):
        return self.returncode


class ScriptPlayerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='harmonica-script-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        exe=self.root/'third_party/AutoHotkey/AutoHotkey64.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'not executed')
        self.script=self.root/'演奏脚本.ahk'
        self.script.write_text('; Harmonica Studio remote protocol: 1\nexample script',encoding='utf-8')
        self.processes=[]
        self.launches=[]
        def launch(args,**kwargs):
            process=FakeProcess()
            self.processes.append(process)
            self.launches.append(args)
            return process
        self.addCleanup(patch.stopall)
        patch('harmonica_studio.playback.resource_root',return_value=self.root).start()
        patch('harmonica_studio.playback.subprocess.Popen',side_effect=launch).start()
        self.player=ScriptPlayer(self.root/'control')

    def command(self):
        return json.loads(self.player.command_file.read_text(encoding='utf-8'))

    def publish(self,request_id=None,**changes):
        value=dict(request_id=self.player._command_id if request_id is None else request_id,
                   state='playing',position=1.25,duration=10,message='正在演奏。')
        value.update(changes)
        self.player.status_file.write_text(json.dumps(value),encoding='utf-8')

    def test_start_is_armed_without_any_play_command(self):
        self.assertEqual(self.player.status['state'],'idle')
        self.player.start(self.script)
        self.assertTrue(self.player.alive)
        self.assertEqual(self.player.status['state'],'ready')
        self.assertFalse(self.player.command_file.exists())
        self.assertEqual(self.launches[0][2],str(self.script.resolve()))
        self.assertEqual(self.launches[0][3:],list(map(str,(
            self.player.stop_file,self.player.command_file,self.player.status_file))))
        with self.assertRaisesRegex(RuntimeError,'已有演奏器'):
            self.player.start(self.script)

    def test_newest_stop_supersedes_play_before_process_reads_it(self):
        self.player.play(self.script)
        self.assertEqual(self.command(),dict(id=1,action='play'))
        self.player.stop_playback()
        self.assertEqual(self.command(),dict(id=2,action='stop'))
        self.assertTrue(self.player.alive)
        self.assertFalse(self.player.stop_file.exists())
        self.publish(request_id=1,state='playing',position=3)
        self.assertEqual(self.player.status['state'],'ready')
        self.assertEqual(self.player.status['position'],0)
        self.publish(state='ready',position=0,message='已停止。')
        self.assertEqual(self.player.status['message'],'已停止。')

    def test_same_score_can_play_again_without_restarting_process(self):
        self.player.play(self.script)
        self.publish()
        self.assertEqual(self.player.status['position'],1.25)
        self.player.play(self.script)
        self.assertEqual(len(self.launches),1)
        self.assertEqual(self.command(),dict(id=2,action='play'))
        self.publish()
        snapshot=self.player.status
        snapshot['state']='untrusted caller mutation'
        self.assertEqual(self.player.status['state'],'playing')

    def test_changed_score_waits_for_released_process_before_restart(self):
        self.player.play(self.script)
        old_stop=self.player.stop_file
        old_directory=old_stop.parent
        self.script.write_text('; Harmonica Studio remote protocol: 1\nnew event table',encoding='utf-8')
        self.player.play(self.script)
        self.assertTrue(old_stop.exists())
        self.assertEqual(len(self.launches),1)
        self.processes[0].returncode=0
        self.player.reap()
        self.assertEqual(len(self.launches),2)
        self.assertNotEqual(self.player.stop_file.parent,old_directory)
        self.assertFalse(old_directory.exists())
        self.assertEqual(self.command(),dict(id=1,action='play'))

    def test_stop_and_close_cancel_a_pending_score_restart(self):
        for operation in ('stop','stop_playback'):
            with self.subTest(operation=operation):
                self.player.play(self.script)
                self.script.write_text('; Harmonica Studio remote protocol: 1\n'+operation,encoding='utf-8')
                self.player.play(self.script)
                count=len(self.launches)
                getattr(self.player,operation)()
                self.processes[-1].returncode=0
                self.player.reap()
                self.assertEqual(len(self.launches),count)
                self.assertFalse(self.player.alive)
                self.assertEqual(self.player.status['state'],'idle')

    def test_play_requested_during_exit_is_deferred_not_lost(self):
        self.player.start(self.script)
        self.player.stop()
        self.player.play(self.script)
        self.assertEqual(len(self.launches),1)
        self.processes[0].returncode=0
        self.player.reap()
        self.assertEqual(len(self.launches),2)
        self.assertEqual(self.command()['action'],'play')

    def test_reap_preserves_other_instance_files(self):
        self.player.start(self.script)
        other=self.player.control_dir/'unrelated.json'
        other.write_text('preserve')
        old_status=self.player.status_file
        self.publish()
        self.player.stop()
        self.assertEqual(self.player.status['state'],'idle')
        self.processes[-1].returncode=0
        self.player.reap()
        self.assertFalse(old_status.exists())
        self.assertEqual(other.read_text(),'preserve')
        self.player.stop_playback()
        self.player.stop()
        self.player.reap()

    def test_malformed_stale_and_nonfinite_status_are_ignored(self):
        self.player.play(self.script)
        self.publish()
        expected=self.player.status
        for raw in ('{','[]',json.dumps({'request_id':1,'state':'playing','position':'bad'}),
                    json.dumps({'request_id':1,'state':'playing','position':float('nan')}),
                    json.dumps({'request_id':1,'state':'playing','position':-1}),
                    json.dumps({'request_id':1,'state':'wrong','position':1}), ' '*20000):
            with self.subTest(raw=raw[:50]):
                self.player.status_file.write_text(raw,encoding='utf-8')
                self.assertEqual(self.player.status,expected)
        self.publish(request_id=0,position=5)
        self.assertEqual(self.player.status,expected)
        self.publish(position=20)
        self.assertEqual(self.player.status['position'],10)

    def test_failed_atomic_write_never_advances_request_or_loses_prior_command(self):
        self.player.play(self.script)
        with patch('harmonica_studio.playback.os.replace',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                self.player.stop_playback()
        self.assertEqual(self.command(),dict(id=1,action='play'))
        self.assertEqual(self.player._command_id,1)
        self.assertEqual(list(self.player.command_file.parent.glob('*.tmp')),[])

    def test_missing_script_and_failed_launch_do_not_leave_armed_state(self):
        with self.assertRaisesRegex(RuntimeError,'找不到演奏脚本'):
            self.player.play(self.root/'missing.ahk')
        with patch('harmonica_studio.playback.subprocess.Popen',side_effect=OSError('failed')):
            with self.assertRaisesRegex(RuntimeError,'无法启动演奏器'):
                self.player.start(self.script)
        self.assertFalse(self.player.alive)
        self.assertEqual(list(self.player.control_dir.iterdir()),[])

    def test_legacy_script_can_be_armed_but_remote_play_explains_reexport(self):
        self.script.write_text('legacy script',encoding='utf-8')
        self.player.start(self.script)
        self.assertTrue(self.player.alive)
        with self.assertRaisesRegex(RuntimeError,'重新导出'):
            self.player.play(self.script)
        self.assertFalse(self.player.command_file.exists())


if __name__=='__main__':
    unittest.main()
