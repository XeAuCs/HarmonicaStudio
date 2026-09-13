import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from harmonica_studio import remote
from harmonica_studio.remote import RemoteServer


class RemoteHTTPTests(unittest.TestCase):
    def setUp(self):
        self.commands = []
        self.handler_threads = []
        self.result = {'ok': True, 'message': '已完成'}
        def handler(command):
            self.commands.append(command)
            self.handler_threads.append(threading.get_ident())
            return self.result
        self.remote = RemoteServer(handler)
        self.port = self.remote.start('127.0.0.1')
        self.token = self.remote.token
        self.addCleanup(self.remote.stop)

    def request(self, method='GET', path='/api/state', body=None, headers=None, token=True, *, send_body=True):
        request_headers = {'Authorization': 'Bearer ' + self.token} if token else {}
        request_headers.update(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body).encode('utf-8')
            request_headers.setdefault('Content-Type', 'application/json')
        if not send_body:
            # Header rejections must arrive without waiting for payload bytes.
            # Sending a late body after that rejection races Windows socket close.
            request_headers.setdefault('Content-Length', str(len(body or b'')))
            body = None
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=4)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def test_pairing_token_is_fragment_and_rotates_after_restart(self):
        address = self.remote.url('127.0.0.1')
        self.assertIn('/#token=' + self.token, address)
        self.assertGreaterEqual(len(self.token), 40)
        old_port = self.port
        self.remote.stop()
        self.assertFalse(self.remote.active)
        self.assertEqual(self.remote.token, '')
        self.assertEqual(self.remote.url('127.0.0.1'), '')
        self.assertEqual(self.remote.port, 0)
        self.port = self.remote.start('127.0.0.1', old_port)
        self.assertNotEqual(self.token, self.remote.token)
        self.assertEqual(self.request()[0], 401)
        self.token = self.remote.token
        self.assertEqual(self.request()[0], 200)

    def test_stop_closes_listener_and_is_idempotent(self):
        self.remote.stop()
        self.remote.stop()
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', self.port), timeout=.5)

    def test_api_requires_authorization_header_not_query_or_cookie(self):
        for headers, path in [({}, '/api/state'), ({'Cookie': 'token=' + self.token}, '/api/state'),
                              ({}, '/api/state?token=' + self.token),
                              ({'Authorization': 'Bearer wrong'}, '/api/state')]:
            with self.subTest(headers=headers, path=path):
                status, response_headers, body = self.request(headers=headers, path=path, token=False)
                self.assertEqual(status, 401)
                self.assertNotIn(self.token.encode(), body)
                self.assertNotIn('Access-Control-Allow-Origin', response_headers)
        self.assertIsNone(self.remote.last_client_at)

    def test_state_is_serialized_snapshot_and_records_authenticated_visit(self):
        state = {'title': '春日影', 'position': 2.75, 'library': [{'id': 'x', 'title': '练习曲'}]}
        self.remote.publish(state)
        state['library'][0]['title'] = 'changed'
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['library'][0]['title'], '练习曲')
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertLess(abs(time.time() - self.remote.last_client_at), 2)
        with self.assertRaises(ValueError):
            self.remote.publish({'position': float('nan')})
        self.assertEqual(self.request()[2], body)

    def test_score_requires_token_host_and_same_origin(self):
        path = '/api/score'
        self.assertEqual(self.request(path=path, token=False)[0], 401)
        self.assertEqual(self.request(path=path, headers={'Authorization': 'Bearer wrong'})[0], 401)
        self.assertEqual(self.request(path=path, headers={'Host': f'evil.example:{self.port}'})[0], 403)
        self.assertEqual(self.request(path=path, headers={'Origin': 'https://other.example'})[0], 403)
        origin = f'http://127.0.0.1:{self.port}'
        status, headers, body = self.request(path=path, headers={'Origin': origin})
        self.assertEqual(status, 200)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(json.loads(body), {'id': 'empty', 'notes': [], 'duration': 0, 'low': 60, 'high': 72})

    def test_score_snapshot_is_independent_of_mutation_and_player_state(self):
        score = {'id': 'revision-1', 'notes': [[0, .5, 60], [.75, 1.2, 64]],
                 'duration': 1.2, 'low': 60, 'high': 64}
        self.remote.publish_score(score)
        self.remote.publish({'score_id': score['id'], 'position': .25})
        score['notes'][0][2] = 85
        score['notes'].append([2, 3, 72])
        status, _, body = self.request(path='/api/score')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['notes'], [[0, .5, 60], [.75, 1.2, 64]])
        self.assertEqual(json.loads(self.request()[2]), {'score_id': 'revision-1', 'position': .25})
        with self.assertRaises(ValueError):
            self.remote.publish_score({'duration': float('nan')})
        self.assertEqual(self.request(path='/api/score')[2], body)
        self.remote.publish_score({'id': 'revision-2', 'notes': [], 'duration': 0, 'low': 48, 'high': 85})
        self.assertEqual(json.loads(self.request(path='/api/score')[2])['id'], 'revision-2')

    def test_score_endpoint_cannot_read_disk_or_accept_file_paths(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('Must not read files')):
            self.assertEqual(self.request(path='/api/score')[0], 200)
            for path, expected in [('/api/score?path=C:/private/song.mid', 400),
                                   ('/api/score?file=../secret', 400),
                                   ('/api/score/C:/private/song.mid', 404),
                                   ('/api/score/../../secret', 404)]:
                with self.subTest(path=path):
                    status, _, body = self.request(path=path)
                    self.assertEqual(status, expected)
                    self.assertNotIn(b'private', body)
                    self.assertNotIn(b'secret', body)

    def test_host_and_origin_must_match_bound_local_address(self):
        origin = f'http://127.0.0.1:{self.port}'
        self.assertEqual(self.request(headers={'Origin': origin})[0], 200)
        cases = [
            {'Host': f'evil.example:{self.port}'},
            {'Host': '127.0.0.1:1'},
            {'Host': f'user@127.0.0.1:{self.port}'},
            {'Origin': 'null'}, {'Origin': 'https://127.0.0.1:' + str(self.port)},
            {'Origin': f'http://evil.example:{self.port}'},
            {'Origin': origin + '/extra'}, {'Origin': 'http://127.0.0.1:1'},
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.request(headers=headers)[0], 403)
                self.assertEqual(self.request('POST', '/api/command', {'action': 'stop'}, headers, send_body=False)[0], 403)
        self.assertEqual(self.commands, [])

    def test_allowed_hosts_is_explicit_and_does_not_allow_other_names(self):
        self.remote.stop()
        self.port = self.remote.start('127.0.0.1', allowed_hosts={'studio.local'})
        self.token = self.remote.token
        self.assertEqual(self.request(headers={'Host': f'studio.local:{self.port}'})[0], 200)
        self.assertEqual(self.request(headers={'Host': f'other.local:{self.port}'})[0], 403)
        self.assertIn('studio.local', self.remote.url('studio.local'))
        with self.assertRaises(ValueError):
            self.remote.url('other.local')

    def test_only_fixed_static_assets_are_served_without_auth(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(remote, 'ASSET_ROOT', Path(folder)):
            Path(folder, 'remote.html').write_text('<main>手机遥控</main>', encoding='utf-8')
            Path(folder, 'remote.css').write_text('body{}', encoding='utf-8')
            Path(folder, 'remote.js').write_text('let paired=true;', encoding='utf-8')
            Path(folder, 'secret.txt').write_text('never publish', encoding='utf-8')
            for path in ('/', '/remote.html', '/remote.css', '/remote.js?v=1'):
                self.assertEqual(self.request(path=path, token=False)[0], 200)
            for path in ('/secret.txt', '/../secret.txt', '/%2e%2e/secret.txt', '/assets/remote.html', '/C:/Windows/win.ini'):
                status, _, body = self.request(path=path, token=False)
                self.assertEqual(status, 404)
                self.assertNotIn(b'never publish', body)
                self.assertNotIn(folder.encode(), body)
        status, _, body = self.request(path='/unknown', token=False)
        self.assertEqual(status, 404)

    def test_valid_commands_are_normalized_and_dispatched_on_request_thread(self):
        commands = [{'action': action} for action in ('play', 'pause', 'stop', 'refresh', 'game_play', 'game_stop')]
        commands += [{'action': 'select', 'song_id': 'song-1'},
                     {'action': 'select', 'song_id': 'song-2', 'autoplay': True},
                     {'action': 'seek', 'position': 12.25}]
        for command in commands:
            self.assertEqual(self.request('POST', '/api/command', command)[0], 200)
        self.assertEqual(self.commands[6]['autoplay'], False)
        self.assertEqual(self.commands[7]['autoplay'], True)
        self.assertEqual(self.commands[-1]['position'], 12.25)
        self.assertTrue(all(ident != threading.get_ident() for ident in self.handler_threads))

    def test_command_validation_rejects_unknown_action_extra_keys_and_bad_values(self):
        commands = [{'action': 'shell', 'command': 'test'}, {'action': 'play', 'path': 'x'},
                    {'action': 'select'}, {'action': 'select', 'song_id': ''},
                    {'action': 'select', 'song_id': 4}, {'action': 'select', 'song_id': 's', 'autoplay': 1},
                    {'action': 'seek', 'position': -1}, {'action': 'seek', 'position': True},
                    {'action': 'seek', 'position': '2'}, {'action': 'seek', 'position': float('nan')},
                    {'action': 'seek', 'position': float('inf')}, {'action': 'game_play', 'script': 'x'}]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.request('POST', '/api/command', command)[0], 400)
        self.assertEqual(self.commands, [])

    def test_malformed_json_and_oversized_body_never_reach_handler(self):
        headers = {'Content-Type': 'application/json'}
        for body in (b'{', b'[]', b'null', b'123', b'\xff', b'{"action":NaN}'):
            self.assertEqual(self.request('POST', '/api/command', body, headers)[0], 400)
        self.assertEqual(self.request('POST', '/api/command', b'x' * (remote.MAX_BODY_BYTES + 1), headers, send_body=False)[0], 413)
        self.assertEqual(self.request('POST', '/api/command', b'{}', {'Content-Type': 'text/plain'}, send_body=False)[0], 415)
        self.assertEqual(self.commands, [])

    def test_declared_oversized_body_is_rejected_before_reading(self):
        connection = socket.create_connection(('127.0.0.1', self.port), timeout=2)
        try:
            request = (f'POST /api/command HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                       f'Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n'
                       'Content-Length: 9999999999999999999999\r\n\r\n')
            connection.sendall(request.encode())
            self.assertIn(b'413', connection.recv(2048).split(b'\r\n')[0])
        finally:
            connection.close()
        self.assertEqual(self.commands, [])

    def test_incomplete_body_times_out_without_dispatch(self):
        self.remote.stop()
        with patch.object(remote, 'SOCKET_TIMEOUT', .15):
            self.port = self.remote.start('127.0.0.1')
            self.token = self.remote.token
            with socket.create_connection(('127.0.0.1', self.port), timeout=2) as connection:
                request = (f'POST /api/command HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                           f'Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n'
                           'Content-Length: 50\r\n\r\n{')
                connection.sendall(request.encode())
                self.assertIn(b'408', connection.recv(2048).split(b'\r\n')[0])
        self.assertEqual(self.commands, [])

    def test_request_authenticated_before_stop_cannot_dispatch_after_restart(self):
        command = b'{"action":"play"}'
        with socket.create_connection(('127.0.0.1', self.port), timeout=2) as connection:
            request = (f'POST /api/command HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                       f'Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n'
                       f'Content-Length: {len(command)}\r\n\r\n')
            connection.sendall(request.encode() + command[:1])
            deadline = time.monotonic() + 1
            while self.remote.last_client_at is None and time.monotonic() < deadline:
                time.sleep(.005)
            self.assertIsNotNone(self.remote.last_client_at)
            self.remote.stop()
            self.port = self.remote.start('127.0.0.1')
            connection.sendall(command[1:])
            self.assertIn(b'503', connection.recv(2048).split(b'\r\n')[0])
        self.assertEqual(self.commands, [])

    def test_duplicate_security_headers_and_chunked_requests_are_rejected(self):
        cases = [('Host: evil.example\r\n', 403),
                 (f'Authorization: Bearer {self.token}\r\n', 401),
                 ('Content-Length: 2\r\n', 400), ('Transfer-Encoding: chunked\r\n', 400)]
        for extra, expected in cases:
            with socket.create_connection(('127.0.0.1', self.port), timeout=2) as connection:
                request = (f'POST /api/command HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                           f'Authorization: Bearer {self.token}\r\nContent-Type: application/json\r\n'
                           f'Content-Length: 2\r\n{extra}\r\n{{}}')
                connection.sendall(request.encode())
                self.assertIn(str(expected).encode(), connection.recv(2048).split(b'\r\n')[0])
        self.assertEqual(self.commands, [])

    def test_command_rate_limit_keeps_state_available(self):
        with self.remote._lock:
            self.remote._command_tokens = 2
        with patch.object(remote, 'COMMANDS_PER_SECOND', 0):
            for _ in range(2):
                self.assertEqual(self.request('POST', '/api/command', {'action': 'play'})[0], 200)
            status, headers, _ = self.request('POST', '/api/command', {'action': 'play'})
            self.assertEqual(status, 429)
            self.assertEqual(headers['Retry-After'], '1')
            self.assertEqual(self.request()[0], 200)
        self.assertEqual(len(self.commands), 2)

    def test_handler_rejection_or_failure_returns_clean_error(self):
        self.result = {'ok': False, 'message': '歌曲仍在准备中'}
        status, _, body = self.request('POST', '/api/command', {'action': 'play'})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)['message'], '歌曲仍在准备中')
        def fail(command):
            raise RuntimeError(r'C:\private\secret.json')
        self.remote._handler = fail
        status, _, body = self.request('POST', '/api/command', {'action': 'play'})
        self.assertEqual(status, 503)
        self.assertNotIn(b'private', body)

    def test_request_concurrency_is_bounded(self):
        self.remote.stop()
        with patch.object(remote, 'MAX_CLIENTS', 2):
            self.port = self.remote.start('127.0.0.1')
        self.token = self.remote.token
        connections = []
        try:
            for _ in range(2):
                connections.append(socket.create_connection(('127.0.0.1', self.port), timeout=2))
            deadline = time.monotonic() + 1
            while self.remote._server.clients._value != 0 and time.monotonic() < deadline:
                time.sleep(.005)
            with socket.create_connection(('127.0.0.1', self.port), timeout=2) as rejected:
                self.assertIn(b'503', rejected.recv(2048).split(b'\r\n')[0])
        finally:
            for connection in connections:
                connection.close()
        deadline = time.monotonic() + 1
        while self.remote._server.clients._value != 2 and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(self.request()[0], 200)


if __name__ == '__main__':
    unittest.main()
