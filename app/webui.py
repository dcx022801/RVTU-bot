#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Docker 多用户 Web 管理页（端口 11451）。

仅使用标准库；管理员账号在首次启动时生成，明文只打印一次，磁盘只保存哈希。
所有 API 使用 Bearer Token（不依赖 Cookie），避免 CSRF。
"""
import json
import os
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import profiles

WEB_HTML = Path(__file__).with_name('webui.html')
PORT = int(os.environ.get('RVTU_WEB_PORT', '11451') or 11451)
HOST = os.environ.get('RVTU_WEB_HOST', '0.0.0.0') or '0.0.0.0'
TOKEN_TTL = 12 * 3600
LOG_FILES = {'multi.log', 'checkin.log', 'happy_checkin.log',
             'portal-auth.log', 'driver.log', 'notify.log'}
SESSIONS = {}
FAILS = {}
_LOCK = threading.RLock()


class WebApp:
    def __init__(self):
        profiles.ensure_dirs()
        self.migrated = profiles.ensure_default_profile()
        self.admin = profiles.ensure_admin()

    def print_banner(self):
        if self.admin:
            username, password = self.admin
            print('=' * 64, flush=True)
            print(' RVTU-bot Web 管理员已创建（仅显示一次，请立即保存）', flush=True)
            print(f' 用户名: {username}', flush=True)
            print(f' 密码:   {password}', flush=True)
            print(f' 地址:   http://<服务器IP>:{PORT}', flush=True)
            print(' 忘记密码：删除 /state/web-admin.json 后重启容器可重新生成', flush=True)
            print('=' * 64, flush=True)
        if self.migrated:
            profile, source = self.migrated
            print(f'[web] 已迁移旧配置为默认用户：{profile["slug"]}'
                  + (f'（来源 {source}）' if source else '（模板，未启用）'), flush=True)

    def login(self, username, password, ip):
        if not self._allowed(ip):
            return None
        if not profiles.verify_admin(username, password):
            self._fail(ip)
            return None
        self._clear(ip)
        token = secrets.token_urlsafe(32)
        with _LOCK:
            SESSIONS[token] = {'username': username, 'expires': time.time() + TOKEN_TTL}
        return token

    def logout(self, token):
        with _LOCK:
            SESSIONS.pop(token, None)

    def auth(self, header):
        if not header or not header.startswith('Bearer '):
            return None
        token = header[7:].strip()
        if not token:
            return None
        with _LOCK:
            item = SESSIONS.get(token)
            if not item:
                return None
            if item['expires'] < time.time():
                SESSIONS.pop(token, None)
                return None
            return item['username']

    def _allowed(self, ip):
        now = time.time()
        with _LOCK:
            items = [t for t in FAILS.get(ip, []) if now - t < 300]
            FAILS[ip] = items
            return len(items) < 8

    def _fail(self, ip):
        with _LOCK:
            FAILS.setdefault(ip, []).append(time.time())

    def _clear(self, ip):
        with _LOCK:
            FAILS.pop(ip, None)


def status_payload():
    profiles_list = profiles.list_profiles(validate=True)
    state = profiles.read_multi_state()
    tasks = state.get('tasks') or {}
    heartbeats = state.get('heartbeats') or {}
    now = time.time()
    for item in profiles_list:
        slug = item['slug']
        item['task_states'] = tasks.get(slug) or {}
        hb = heartbeats.get(slug)
        item['heartbeat'] = hb
        item['heartbeat_age'] = max(0, int(now - hb)) if hb else None
    try:
        hb_age = max(0, int(now - os.path.getmtime(Path(profiles.STATE_DIR) / 'heartbeat')))
    except Exception:
        hb_age = None
    return {
        'date': state.get('date') or '',
        'orders': state.get('orders') or {},
        'heartbeat_age': hb_age,
        'users': profiles_list,
    }


def user_payload(slug):
    info = profiles.get_profile(slug, validate=True)
    state = profiles.read_multi_state()
    info['task_states'] = (state.get('tasks') or {}).get(slug) or {}
    hb = (state.get('heartbeats') or {}).get(slug)
    info['heartbeat'] = hb
    info['heartbeat_age'] = max(0, int(time.time() - hb)) if hb else None
    info['config'] = profiles.read_config(slug)
    return info


class Handler(BaseHTTPRequestHandler):
    server_version = 'RVTU-bot/1.0'
    app = None

    def log_message(self, fmt, *args):
        print(f'[web] {self.address_string()} {fmt % args}', flush=True)

    def _send(self, status, body: bytes, content_type='application/json; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if content_type.startswith('text/html'):
            self.send_header(
                'Content-Security-Policy',
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status=200):
        self._send(status, json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _html(self):
        try:
            data = WEB_HTML.read_bytes()
        except Exception as exc:
            self._json({'error': f'页面缺失：{exc}'}, 500)
            return
        self._send(200, data, 'text/html; charset=utf-8')

    def _body(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except Exception:
            return None
        if length < 0 or length > 2 * 1024 * 1024:
            return None
        try:
            raw = self.rfile.read(length)
            return json.loads(raw.decode('utf-8')) if raw else {}
        except Exception:
            return None

    def _auth(self):
        username = self.app.auth(self.headers.get('Authorization'))
        if not username:
            self._json({'error': '未登录或会话已过期'}, 401)
            return None
        return username

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path in ('/', '/index.html'):
            return self._html()
        if path == '/healthz':
            return self._json({'ok': True})
        if path == '/api/users':
            if not self._auth():
                return
            return self._json(status_payload())
        if path.startswith('/api/users/'):
            if not self._auth():
                return
            parts = path.strip('/').split('/')
            if len(parts) != 3:
                return self._json({'error': '路径错误'}, 404)
            try:
                return self._json(user_payload(parts[2]))
            except Exception as exc:
                return self._json({'error': str(exc)}, 404)
        if path.startswith('/api/logs/'):
            if not self._auth():
                return
            parts = path.strip('/').split('/')
            if len(parts) != 3:
                return self._json({'error': '路径错误'}, 404)
            query = urllib.parse.parse_qs(parsed.query)
            name = (query.get('file') or ['multi.log'])[0]
            if name not in LOG_FILES:
                return self._json({'error': '不允许的日志文件'}, 400)
            try:
                lines = int((query.get('lines') or ['300'])[0])
            except Exception:
                lines = 300
            lines = max(1, min(lines, 2000))
            slug = parts[2]
            try:
                profiles.profile_path(slug)
                log_path = Path(profiles.USERS_DIR) / slug / 'state' / 'logs' / name
                if not log_path.is_file():
                    return self._json({'content': '', 'file': name})
                text = log_path.read_text(encoding='utf-8', errors='replace')
                return self._json({'file': name,
                                   'content': '\n'.join(text.splitlines()[-lines:])})
            except Exception as exc:
                return self._json({'error': str(exc)}, 404)
        if path == '/api/me':
            username = self._auth()
            if username:
                return self._json({'username': username})
            return
        return self._json({'error': 'not found'}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == '/api/login':
            body = self._body()
            if not isinstance(body, dict):
                return self._json({'error': '请求格式错误'}, 400)
            ip = self.client_address[0]
            token = self.app.login(str(body.get('username') or ''),
                                   str(body.get('password') or ''), ip)
            if not token:
                return self._json({'error': '用户名或密码错误（或尝试过于频繁）'}, 401)
            return self._json({'token': token})
        if path == '/api/logout':
            header = self.headers.get('Authorization') or ''
            self.app.logout(header[7:].strip() if header.startswith('Bearer ') else '')
            return self._json({'ok': True})
        if path == '/api/users':
            if not self._auth():
                return
            body = self._body()
            if not isinstance(body, dict):
                return self._json({'error': '请求格式错误'}, 400)
            try:
                profile = profiles.create_profile(
                    str(body.get('name') or '').strip(),
                    str(body.get('slug') or '').strip() or None,
                    enabled=False)
                return self._json(profile, 201)
            except Exception as exc:
                return self._json({'error': str(exc)}, 400)
        if path == '/api/reload':
            if not self._auth():
                return
            profiles.touch_reload()
            return self._json({'ok': True})
        return self._json({'error': 'not found'}, 404)

    def do_PUT(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if not path.startswith('/api/users/'):
            return self._json({'error': 'not found'}, 404)
        if not self._auth():
            return
        parts = path.strip('/').split('/')
        if len(parts) != 3:
            return self._json({'error': '路径错误'}, 404)
        slug = parts[2]
        body = self._body()
        if not isinstance(body, dict):
            return self._json({'error': '请求格式错误'}, 400)
        config = body.get('config')
        if not isinstance(config, dict):
            return self._json({'error': 'config 必须是对象'}, 400)
        name = str(body.get('name') or '').strip()
        if not name:
            return self._json({'error': '用户名称不能为空'}, 400)
        tasks = body.get('tasks')
        if tasks is not None and not isinstance(tasks, dict):
            return self._json({'error': 'tasks 必须是对象'}, 400)
        if tasks is None:
            try:
                tasks = profiles.get_profile(slug)['tasks']
            except Exception as exc:
                return self._json({'error': str(exc)}, 404)
        errors, warnings = profiles.validate_data(config, tasks)
        if errors:
            return self._json({'error': '配置未通过校验',
                               'errors': errors, 'warnings': warnings}, 400)
        try:
            profiles.write_config(slug, config)
            profiles.save_meta(slug, name, bool(body.get('enabled', False)),
                               tasks=tasks)
            profiles.touch_reload()
            return self._json({'ok': True, 'warnings': warnings,
                               'user': user_payload(slug)})
        except Exception as exc:
            return self._json({'error': str(exc)}, 400)

    def do_DELETE(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if not path.startswith('/api/users/'):
            return self._json({'error': 'not found'}, 404)
        if not self._auth():
            return
        parts = path.strip('/').split('/')
        if len(parts) != 3:
            return self._json({'error': '路径错误'}, 404)
        try:
            target = profiles.delete_profile(parts[2])
            return self._json({'ok': True, 'trash': target})
        except Exception as exc:
            return self._json({'error': str(exc)}, 400)


def create_server():
    profiles.ensure_dirs()
    app = WebApp()
    app.print_banner()
    Handler.app = app
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f'[web] 管理页已启动：http://{HOST}:{PORT}/', flush=True)
    return server


def serve(server, stop_event=None):
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        try:
            server.server_close()
        except Exception:
            pass


if __name__ == '__main__':
    srv = create_server()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
