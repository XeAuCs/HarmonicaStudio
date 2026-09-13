"""Small, authenticated LAN remote. All player work stays in the caller's handler.

The URL fragment pairs a browser without putting its secret in HTTP URLs/logs.
Only the three bundled web assets and three explicit API routes are exposed.
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import math
from pathlib import Path
import secrets
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore, Lock, Thread
import time
from urllib.parse import urlsplit


MAX_BODY_BYTES = 4096
MAX_CLIENTS = 12
SOCKET_TIMEOUT = 3.0
COMMAND_BURST = 20
COMMANDS_PER_SECOND = 8.0
ASSET_ROOT = Path(__file__).parent / 'assets'
_ASSETS = {
    '/': ('remote.html', 'text/html; charset=utf-8'),
    '/remote.html': ('remote.html', 'text/html; charset=utf-8'),
    '/remote.css': ('remote.css', 'text/css; charset=utf-8'),
    '/remote.js': ('remote.js', 'text/javascript; charset=utf-8'),
}
_ACTIONS = {'select', 'play', 'pause', 'stop', 'seek', 'refresh', 'game_play', 'game_stop'}


def _host_name(value: str) -> str:
    value = value.strip().lower().rstrip('.')
    try:
        return ipaddress.ip_address(value).compressed
    except ValueError:
        return value


def local_addresses() -> list[str]:
    """Local addresses only; never contact a discovery or public Internet server."""
    addresses = {'127.0.0.1', '::1'}
    try:
        addresses.update(item[4][0] for item in socket.getaddrinfo(socket.gethostname(), None))
    except OSError:
        pass
    return sorted({_host_name(address) for address in addresses})


def _command(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('操作内容必须是一个对象。')
    action = payload.get('action')
    if not isinstance(action, str) or action not in _ACTIONS:
        raise ValueError('不支持这个操作。')
    allowed = {'action'}
    result = {'action': action}
    if action == 'select':
        allowed.update({'song_id', 'autoplay'})
        song_id = payload.get('song_id')
        if not isinstance(song_id, str) or not song_id or len(song_id) > 512:
            raise ValueError('请选择有效的歌曲。')
        autoplay = payload.get('autoplay', False)
        if not isinstance(autoplay, bool):
            raise ValueError('自动播放选项必须是开或关。')
        result.update(song_id=song_id, autoplay=autoplay)
    elif action == 'seek':
        position = payload.get('position')
        if (isinstance(position, bool) or not isinstance(position, (int, float))
                or not math.isfinite(position) or position < 0):
            raise ValueError('播放位置必须是有效的非负秒数。')
        allowed.add('position')
        result['position'] = float(position)
    if set(payload) - allowed:
        raise ValueError('操作包含不支持的参数。')
    return result


class _HTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address, owner):
        self.owner = owner
        self.clients = BoundedSemaphore(MAX_CLIENTS)
        if ':' in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, _Handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(SOCKET_TIMEOUT)
        return connection, address

    def process_request(self, request, client_address):
        if not self.clients.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\n'
                                b'Content-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.clients.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.clients.release()

    def handle_error(self, request, client_address):
        # Network failures must not print tracebacks, local paths or pairing data.
        pass


class _Handler(BaseHTTPRequestHandler):
    server_version = 'HarmonicaRemote'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self._json(code, {'ok': False, 'message': '请求格式不正确或不支持这个操作。'})

    def _send(self, code, body, content_type, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.send_header('Connection', 'close')
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _json(self, code, value, extra=None):
        self._send(code, json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'),
                   'application/json; charset=utf-8', extra)

    def _error(self, code, message):
        self._json(code, {'ok': False, 'message': message})
        return False

    def _authority(self, text):
        try:
            parsed = urlsplit('http://' + text)
            if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                    or parsed.path or parsed.query or parsed.fragment or '\\' in text
                    or any(character.isspace() for character in text)):
                return None
            return parsed.hostname.lower(), parsed.port if parsed.port is not None else 80
        except ValueError:
            return None

    def _valid_request(self, authenticated=False):
        owner = self.server.owner
        with owner._lock:
            if owner._server is not self.server:
                return self._error(503, '手机遥控已关闭，请在电脑上重新开启。')
            token, allowed_hosts, port = owner._token, owner._allowed_hosts, owner._port
        hosts = self.headers.get_all('Host', [])
        authority = self._authority(hosts[0]) if len(hosts) == 1 else None
        if not authority or _host_name(authority[0]) not in allowed_hosts or authority[1] != port:
            return self._error(403, '访问地址不正确，请重新扫描电脑上的二维码。')
        origins = self.headers.get_all('Origin', [])
        if origins:
            if len(origins) != 1 or not origins[0].startswith('http://'):
                return self._error(403, '只允许当前遥控页面发送操作。')
            if self._authority(origins[0][7:]) != authority:
                return self._error(403, '只允许当前遥控页面发送操作。')
        if authenticated:
            auth_headers = self.headers.get_all('Authorization', [])
            supplied = auth_headers[0] if len(auth_headers) == 1 else ''
            if not hmac.compare_digest(supplied.encode('utf-8'), ('Bearer ' + token).encode('utf-8')):
                return self._error(401, '连接凭证已失效，请重新扫描电脑上的二维码。')
            with owner._lock:
                if owner._server is not self.server or owner._token != token:
                    return self._error(401, '连接凭证已失效，请重新扫描电脑上的二维码。')
                owner._last_client_at = time.time()
        return True

    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        if not self._valid_request(authenticated=path.startswith('/api/')):
            return
        if path == '/api/score':
            if parsed.query:
                self._error(400, '曲谱请求不接受路径或其他参数。')
                return
            with self.server.owner._lock:
                body = self.server.owner._score
            self._send(200, body, 'application/json; charset=utf-8')
            return
        if path == '/api/state':
            with self.server.owner._lock:
                body = self.server.owner._state
            self._send(200, body, 'application/json; charset=utf-8')
            return
        if path not in _ASSETS:
            self._error(404, '没有这个页面。')
            return
        filename, content_type = _ASSETS[path]
        try:
            body = (ASSET_ROOT / filename).read_bytes()
        except OSError:
            self._error(503, '手机页面暂时不可用，请重新打开电脑端。')
            return
        self._send(200, body, content_type)

    def do_POST(self):
        if not self._valid_request(authenticated=True):
            return
        if urlsplit(self.path).path != '/api/command':
            self._error(404, '没有这个操作地址。')
            return
        if self.headers.get_all('Transfer-Encoding', []):
            self._error(400, '不支持分块操作请求。')
            return
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
            self._error(400, '操作请求缺少有效的长度。')
            return
        if len(lengths[0].lstrip('0')) > len(str(MAX_BODY_BYTES)):
            self._error(413, '操作内容太大。')
            return
        length = int(lengths[0].lstrip('0') or '0')
        if length > MAX_BODY_BYTES:
            self._error(413, '操作内容太大。')
            return
        if self.headers.get_content_type() != 'application/json':
            self._error(415, '操作内容需要使用 JSON 格式。')
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self._error(400, '操作内容不完整。')
                return
            def reject_constant(value):
                raise ValueError('不支持的数值。')
            command = _command(json.loads(body.decode('utf-8'), parse_constant=reject_constant))
        except (UnicodeError, ValueError, OverflowError, RecursionError):
            self._error(400, '操作内容不正确，请重新尝试。')
            return
        except (OSError, TimeoutError):
            self._error(408, '接收操作超时，请重新尝试。')
            return
        owner = self.server.owner
        allowed = owner._take_command(self.server)
        if allowed is None:
            self._error(503, '手机遥控已关闭，请在电脑上重新开启。')
            return
        if not allowed:
            self._json(429, {'ok': False, 'message': '操作过于频繁，请稍等一下。'}, {'Retry-After': '1'})
            return
        try:
            result = owner._handler(command)
            if not isinstance(result, dict) or not isinstance(result.get('ok'), bool):
                raise ValueError('Invalid command result')
            result = {'ok': result['ok'], 'message': str(result.get('message', ''))}
        except Exception:
            self._error(503, '电脑暂时未响应，请稍后再试。')
            return
        self._json(200 if result['ok'] else 409, result)


class RemoteServer:
    """Thread-safe state cache and HTTP boundary; handler runs on a request thread.

    ``last_client_at`` is a Unix timestamp, or None before an authenticated visit.
    ``start`` returns the selected port. Closing/reopening rotates the pairing token.
    The command handler must impose its own bounded wait for the GUI thread.
    """

    def __init__(self, command_handler):
        self._handler = command_handler
        self._lock = Lock()
        self._server = None
        self._thread = None
        self._port = 0
        self._token = ''
        self._allowed_hosts = frozenset()
        self._last_client_at = None
        self._state = b'{}'
        self._score = b'{"id":"empty","notes":[],"duration":0,"low":60,"high":72}'
        self._command_tokens = float(COMMAND_BURST)
        self._command_time = time.monotonic()

    @property
    def active(self):
        with self._lock:
            return self._server is not None

    @property
    def port(self):
        with self._lock:
            return self._port

    @property
    def token(self):
        with self._lock:
            return self._token

    @property
    def last_client_at(self):
        with self._lock:
            return self._last_client_at

    def start(self, host='0.0.0.0', port=0, allowed_hosts=None):
        names = {'localhost', *local_addresses()}
        if host not in {'0.0.0.0', '::', ''}:
            names.add(_host_name(host))
        if allowed_hosts:
            names.update(_host_name(value) for value in allowed_hosts)
        with self._lock:
            if self._server is not None:
                raise RuntimeError('手机遥控已经开启。')
            server = _HTTPServer((host, port), self)
            self._port = server.server_address[1]
            self._token = secrets.token_urlsafe(32)
            self._allowed_hosts = frozenset(names)
            self._last_client_at = None
            self._command_tokens = float(COMMAND_BURST)
            self._command_time = time.monotonic()
            self._server = server
            self._thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .1},
                                  name='HarmonicaRemote', daemon=True)
            self._thread.start()
            return self._port

    def url(self, host):
        with self._lock:
            if self._server is None:
                return ''
            if _host_name(host) not in self._allowed_hosts:
                raise ValueError('这不是当前电脑的连接地址。')
            host = '[' + host + ']' if ':' in host and not host.startswith('[') else host
            return f'http://{host}:{self._port}/#token={self._token}'

    def publish(self, state):
        if not isinstance(state, dict):
            raise TypeError('Remote state must be a dictionary')
        data = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        with self._lock:
            self._state = data

    def publish_score(self, score):
        """Cache a complete score snapshot; this API never reads a song file."""
        if not isinstance(score, dict):
            raise TypeError('Remote score must be a dictionary')
        data = json.dumps(score, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        with self._lock:
            self._score = data

    def _take_command(self, server):
        with self._lock:
            if self._server is not server:
                return None
            now = time.monotonic()
            self._command_tokens = min(float(COMMAND_BURST),
                                       self._command_tokens + (now - self._command_time) * COMMANDS_PER_SECOND)
            self._command_time = now
            if self._command_tokens < 1:
                return False
            self._command_tokens -= 1
            return True

    def stop(self):
        with self._lock:
            server, thread = self._server, self._thread
            self._server = self._thread = None
            self._token = ''
            self._port = 0
            self._last_client_at = None
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=2)
