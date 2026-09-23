from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import sqlite3
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .app import BusinessApi
from .db import Database
from .douyin import ProviderError
from .studio import Studio
from .config import load_env


WEB_ROOT = Path(__file__).resolve().parents[2] / 'apps' / 'web'


class RequestHandler(BaseHTTPRequestHandler):
    api: BusinessApi
    studio: Studio
    lock: threading.RLock

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def do_GET(self):
        self._handle('GET')

    def do_POST(self):
        self._handle('POST')

    def do_PATCH(self):
        self._handle('PATCH')

    def _headers(self):
        headers = {key.lower(): value for key, value in self.headers.items()}
        if 'authorization' not in headers:
            cookie = SimpleCookie()
            cookie.load(headers.get('cookie', ''))
            if 'livemake_session' in cookie:
                headers['authorization'] = 'Bearer ' + cookie['livemake_session'].value
        return headers

    def _handle(self, method):
        parsed = urlparse(self.path)
        route = parsed.path.rstrip('/') or '/'
        headers = self._headers()
        try:
            if method != 'GET':
                origin = self.headers.get('Origin')
                expected = urlparse(origin).netloc if origin else None
                if origin and (expected != self.headers.get('Host') or urlparse(origin).scheme not in {'http', 'https'}):
                    raise PermissionError('跨站请求已拒绝。')
                if 'livemake_session=' in self.headers.get('Cookie', '') and self.headers.get('X-Studio-Request') != '1':
                    raise PermissionError('缺少请求验证标记。')
                if self.headers.get('Transfer-Encoding'):
                    raise ValueError('chunked requests are not supported')
            if method == 'GET' and not route.startswith('/api/'):
                static = {'/': 'index.html', '/app.js': 'app.js', '/ai.js': 'ai.js', '/styles.css': 'styles.css'}
                if route not in static:
                    self._json(404, {'error': 'not found'})
                    return
                self._file(WEB_ROOT / static[route], authenticated=False)
                return
            with self.lock:
                if method == 'GET' and route.startswith(('/api/v1/studio/media/', '/api/v1/studio/images/')):
                    self.api._actor(headers)
                    path = self.studio.ai.file(route.split('/')[-1]) if '/images/' in route else self.studio.file(route.split('/')[-1])
                else:
                    path = None
            if path:
                self._file(path, authenticated=True)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0:
                raise ValueError('invalid Content-Length')
            parts = route.strip('/').split('/')
            if method == 'POST' and len(parts) == 6 and parts[:4] == ['api', 'v1', 'studio', 'tasks'] and parts[-1] == 'upload':
                if length > self.studio.max_upload:
                    self._json(413, {'error': '文件超过 100 MiB 上限。'})
                    return
                with self.lock:
                    actor = self.api._actor(headers)
                    result = self.studio.upload(actor, parts[4], unquote(self.headers.get('X-Filename', '')), self.rfile, length)
                self._json(202, result)
                return
            if length > 1024 * 1024:
                self._json(413, {'error': 'JSON request too large'})
                return
            raw = self.rfile.read(length) if length else b'{}'
            body = json.loads(raw.decode('utf-8'))
            if not isinstance(body, dict):
                raise ValueError('request body must be a JSON object')
            with self.lock:
                if route.startswith('/api/v1/studio/'):
                    actor = self.api._actor(headers)
                    query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
                    result = self.studio.dispatch(method, route, actor, body, query)
                    if route.endswith('/douyin/callback'):
                        self.send_response(303)
                        self.send_header('Location', '/?douyin=connected')
                        self.send_header('Content-Length', '0')
                        self.end_headers()
                    else:
                        self._json(200, result)
                    return
                response = self.api.request(method, self.path, headers, body)
            cookie = None
            if route == '/api/v1/auth/login' and response.status == 200:
                cookie = 'livemake_session=' + response.body['access_token'] + '; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800'
            elif route == '/api/v1/auth/logout':
                cookie = 'livemake_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0'
            if cookie and os.environ.get('STUDIO_COOKIE_SECURE') == '1':
                cookie += '; Secure'
            self._json(response.status, response.body, cookie)
        except PermissionError as error:
            self._json(403, {'error': str(error)})
        except KeyError as error:
            self._json(404, {'error': str(error)})
        except (ValueError, UnicodeDecodeError) as error:
            self._json(400, {'error': str(error)})
        except sqlite3.IntegrityError:
            self._json(409, {'error': '记录冲突，请刷新后重试。'})
        except ProviderError as error:
            self._json(502, {'error': str(error)})
        except (ConnectionError, socket.timeout):
            self.close_connection = True
        except Exception:
            import traceback
            traceback.print_exc()
            self._json(500, {'error': '服务处理失败，请检查服务日志。'})

    def _security_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

    def _json(self, status, body, cookie=None):
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else b''
        self.send_response(status)
        self._security_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _file(self, path, authenticated):
        if not path.is_file():
            self._json(404, {'error': 'file not found'})
            return
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        requested = self.headers.get('Range') if authenticated else None
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if match and (match[1] or match[2]):
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]), end) if match[2] else end
                else:
                    start = max(0, size - int(match[2]))
                status = 206
            if not match or not (0 <= start <= end < size) or not (match[1] or match[2]):
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
        self.send_response(status)
        self._security_headers()
        self.send_header('Content-Type', (mimetypes.guess_type(path.name)[0] or 'application/octet-stream') + ('' if authenticated else '; charset=utf-8'))
        self.send_header('Cache-Control', 'private, no-store' if authenticated else 'no-cache')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        if status == 206:
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        if authenticated and 'download=1' in self.path:
            self.send_header('Content-Disposition', 'attachment; filename="' + ('image.png' if path.suffix == '.png' else 'video.mp4') + '"')
        self.end_headers()
        with path.open('rb') as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def log_message(self, format, *args):
        return


def main():
    load_env()
    path = Path(os.environ.get('BUSINESS_API_DATABASE', '.runtime/business-api.sqlite3'))
    path.parent.mkdir(parents=True, exist_ok=True)
    database = Database(path)
    _bootstrap_admin(database)
    lock = threading.RLock()
    api = BusinessApi(database)
    studio = Studio(api, Path(os.environ.get('STUDIO_STORAGE', '.runtime/media')), lock)
    RequestHandler.api, RequestHandler.studio, RequestHandler.lock = api, studio, lock
    host = os.environ.get('BUSINESS_API_HOST', '127.0.0.1')
    port = int(os.environ.get('BUSINESS_API_PORT', '8080'))
    server = ThreadingHTTPServer((host, port), RequestHandler)
    print(f'Live Make: http://{host}:{port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        studio.close()
        database.close()


def _bootstrap_admin(database):
    username = os.environ.get('BUSINESS_API_ADMIN_USERNAME')
    password = os.environ.get('BUSINESS_API_ADMIN_PASSWORD')
    existing = database.connection.execute('SELECT COUNT(*) AS count FROM accounts').fetchone()['count']
    if existing == 0 and username and password:
        database.create_account(username, password, is_admin=True)


if __name__ == '__main__':
    main()
