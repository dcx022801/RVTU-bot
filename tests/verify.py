#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RVTU-bot 离线回归验证（不访问真实学校接口/SMTP）。

用法：
    python3 tests/verify.py

覆盖：静态检查、配置校验、checkin/happy 关键分支、Portal 白名单/URL 解码/
MAC 规范化/204 复核、调度持久化/重试/预检、bootstrap 与 refresh 参数。
"""
import compileall
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / 'app' / 'scripts'
PY = sys.executable
sys.path.insert(0, str(REPO / 'app'))
sys.path.insert(0, str(SCRIPTS))

RESULTS = []


def check(name, condition, detail=''):
    RESULTS.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}" + (f" | {detail}" if detail else ''))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def make_case(placeholder=False):
    root = pathlib.Path(tempfile.mkdtemp(prefix='rvtu-case-'))
    config, defaults, state = root / 'config', root / 'defaults', root / 'state'
    config.mkdir(); defaults.mkdir(); state.mkdir()
    write_json(config / 'creds.json', {
        'user': '<身份证号>' if placeholder else 'fake-id',
        'pass': '<统一密码>' if placeholder else 'fake-pass',
    })
    write_json(config / 'mail.json', {})
    write_json(config / 'macs.json', {'macs': ['AA:BB:CC:DD:EE:01', 'AA:BB:CC:DD:EE:02']})
    write_json(config / 'location.json', {
        'latitude': '35.1', 'longitude': '119.1', 'locationInfo': '模拟地点',
    })
    write_json(config / 'portal.json', {
        'umac': '11:22:33:44:55:66', 'uplinkMac': 'AA:BB:CC:DD:EE:FF',
        'allowedHosts': ['127.0.0.1'], 'allowedSchemes': ['http', 'https'],
        'allowAny': False, 'macFormat': 'auto',
    })
    write_json(defaults / 'api.json', {
        'base': f'http://127.0.0.1:{PORT}/xsxxgl-server',
        'module': 'sskq', 'ua_checkin': 'ua', 'ua_happy': 'ua',
        'probe_url': f'http://127.0.0.1:{PORT}/generate_204',
    })
    write_json(defaults / 'schedule.json', {
        'checkin': {'hour': 21, 'minute': 0, 'random_delay_sec_max': 0,
                    'retry_after_min': 20, 'notify_before_min': 15},
        'happy': {'hour': 19, 'minute': 0, 'random_delay_sec_max': 0,
                  'retry_after_min': 20, 'notify_before_min': 15, 'budget_sec': 55},
        'portal': {'interval_minutes': 1},
    })
    write_json(state / 'session.json', {})
    (state / 'logs').mkdir()
    return root, config, defaults, state


def run_script(script, case, *args):
    root, config, defaults, state = case
    env = os.environ.copy()
    env.update({
        'CONFIG_DIR': str(config),
        'DEFAULTS_DIR': str(defaults),
        'STATE_DIR': str(state),
        'PYTHONIOENCODING': 'utf-8',
    })
    return subprocess.run(
        [PY, str(SCRIPTS / script), *args],
        env=env, capture_output=True, text=True, encoding='utf-8', timeout=40,
    )


# ---------------- mock API / portal ----------------
STATE = {
    'requests': [],
    'checkin_detail': {'studentName': '模拟', 'checkInTime': '', 'status': 0, 'groupName': 'g'},
    'happy_detail': {'studentName': '模拟', 'checkInTime': '', 'workDayToday': True,
                     'canCheckIn': True, 'status': 0, 'venueName': 'v', 'workDays': '1,2,3,4,7'},
    'checkin_post': 'success',
    'happy_post': 'success',
    'checkin_auth_fail': False,
    'logged_in': True,
    'portal_logged': False,
    'portal_location': '',
    'portal_captures': [],
    'portal_probes': 0,
    'portal_login_success': True,
    'portal_still_redirect': False,
}


def reset_state():
    STATE.update({
        'requests': [],
        'checkin_detail': {'studentName': '模拟', 'checkInTime': '', 'status': 0, 'groupName': 'g'},
        'happy_detail': {'studentName': '模拟', 'checkInTime': '', 'workDayToday': True,
                         'canCheckIn': True, 'status': 0, 'venueName': 'v', 'workDays': '1,2,3,4,7'},
        'checkin_post': 'success', 'happy_post': 'success',
        'checkin_auth_fail': False, 'logged_in': True,
        'portal_logged': False, 'portal_location': '',
        'portal_captures': [], 'portal_probes': 0,
        'portal_login_success': True, 'portal_still_redirect': False,
    })


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        STATE['requests'].append(('GET', self.path))
        if self.path.endswith('/mobile/attendance/records/v1/detail'):
            if STATE['checkin_auth_fail'] and not STATE['logged_in']:
                return self._json({'success': False, 'message': 'token 失效'}, 401)
            return self._json({'success': True, 'result': STATE['checkin_detail']})
        if self.path.endswith('/mobile/apattendance/checkin-record/v1/detail'):
            return self._json({'success': True, 'result': STATE['happy_detail']})
        if self.path.endswith('/mobile_auth/public_key'):
            return self._json({'success': True, 'result': 'fake-public-key'})
        if self.path == '/generate_204':
            STATE['portal_probes'] += 1
            if STATE['portal_logged'] and not STATE['portal_still_redirect']:
                self.send_response(204)
                self.end_headers()
                return
            self.send_response(302)
            self.send_header('Location', STATE['portal_location'])
            self.end_headers()
            return
        return self._json({'success': False, 'message': 'not found'}, 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        raw = self.rfile.read(length).decode('utf-8', 'ignore')
        STATE['requests'].append(('POST', self.path))
        if self.path.endswith('/mobile_auth/stu_login'):
            STATE['logged_in'] = True
            return self._json({'success': True, 'result': {'token': 'new-fake-token'}})
        if self.path.endswith('/mobile/attendance/records/v1/check/in'):
            if STATE['checkin_post'] == 'success':
                STATE['checkin_detail']['checkInTime'] = '21:00:05'
                return self._json({'success': True, 'message': 'ok'})
            return self._json({'success': False, 'message': 'fail'})
        if self.path.endswith('/mobile/apattendance/checkin-record/v1/check/in'):
            body = json.loads(raw or '{}')
            method = body.get('checkInMethod')
            if STATE['happy_post'] == 'success':
                STATE['happy_detail']['checkInTime'] = '19:00:05'
                return self._json({'success': True, 'message': 'ok'})
            if STATE['happy_post'] == 'gps' and method == 2:
                STATE['happy_detail']['checkInTime'] = '19:00:06'
                return self._json({'success': True, 'message': 'ok'})
            if STATE['happy_post'] == 'already':
                return self._json({'success': False, 'message': '今日已打卡'})
            return self._json({'success': False, 'message': 'mac fail'})
        if self.path == '/portalauth/login':
            form = parse_qs(raw, keep_blank_values=True)
            STATE['portal_captures'].append({k: v[0] for k, v in form.items()})
            if STATE['portal_login_success']:
                STATE['portal_logged'] = True
                return self._json({'success': True, 'message': 'ok'})
            return self._json({'success': False, 'message': 'bad password'})
        return self._json({'success': False, 'message': 'not found'}, 404)


SERVER = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
PORT = SERVER.server_address[1]
threading.Thread(target=SERVER.serve_forever, daemon=True).start()


# ---------------- tests ----------------
def test_static():
    check('A01 Python 语法', compileall.compile_dir(str(REPO / 'app'), quiet=1))
    for path in list((REPO / 'config').glob('*.json')) + list((REPO / 'defaults').glob('*.json')):
        try:
            json.loads(path.read_text(encoding='utf-8'))
            ok = True
        except Exception:
            ok = False
        check(f'A03 JSON {path.name}', ok)
    sh = subprocess.run(['sh', '-n', str(REPO / 'app' / 'entrypoint.sh')], capture_output=True)
    check('A02 entrypoint.sh 语法', sh.returncode == 0)
    sh = subprocess.run(['sh', '-n', str(SCRIPTS / 'portal-auth.sh')], capture_output=True)
    check('A02 portal-auth.sh 语法', sh.returncode == 0)


def test_config():
    import configutil
    tmp = pathlib.Path(tempfile.mkdtemp())
    base = json.loads((REPO / 'defaults' / 'schedule.json').read_text(encoding='utf-8'))
    for n, expect_warn in ((4, True), (5, True), (6, False)):
        data = json.loads(json.dumps(base))
        data['checkin']['notify_before_min'] = n
        p = tmp / f's{n}.json'
        write_json(p, data)
        parsed = configutil.load_schedule(str(p))
        check(f'F04 notify={n}', (len(parsed['_warnings']) > 0) == expect_warn,
              f"warnings={len(parsed['_warnings'])}")
        if expect_warn:
            check(f'F04 notify={n} 自动关闭', parsed['checkin']['notify_before_min'] == 0)
    data = json.loads(json.dumps(base)); data['checkin']['hour'] = 25
    write_json(tmp / 'hour.json', data)
    try:
        configutil.load_schedule(str(tmp / 'hour.json'))
        check('F11 非法小时', False)
    except configutil.ConfigError:
        check('F11 非法小时', True)
    data = json.loads(json.dumps(base)); data['portal']['interval_minutes'] = 0
    write_json(tmp / 'interval.json', data)
    check('F11 非法间隔钳制', configutil.load_schedule(str(tmp / 'interval.json'))['portal']['interval_minutes'] == 1)
    write_json(tmp / 'mail.json', {'host': 'smtp.example', 'user': 'u'})
    try:
        configutil.load_mail(str(tmp / 'mail.json'))
        check('D01 邮件不完整拒绝', False)
    except configutil.ConfigError:
        check('D01 邮件不完整拒绝', True)
    check('D01 模板邮件视为未启用', configutil.load_mail(str(REPO / 'config' / 'mail.json')) is None)
    write_json(tmp / 'portal.json', {'allowedHosts': ['*']})
    try:
        configutil.load_portal(str(tmp / 'portal.json'))
        check('P0 禁止通配符', False)
    except configutil.ConfigError:
        check('P0 禁止通配符', True)


def test_checkin():
    reset_state()
    case = make_case(placeholder=True)
    before = len(STATE['requests'])
    proc = run_script('checkin.py', case, '--dry-run')
    check('B02 占位符拒绝', proc.returncode == 2 and len(STATE['requests']) == before,
          f'rc={proc.returncode},new_requests={len(STATE["requests"]) - before}')

    reset_state(); case = make_case()
    proc = run_script('checkin.py', case, '--dry-run')
    check('B03 dry-run', proc.returncode == 0 and not any(m == 'POST' for m, _ in STATE['requests']),
          f'rc={proc.returncode}')

    reset_state(); STATE['checkin_detail']['checkInTime'] = '20:00:00'; case = make_case()
    proc = run_script('checkin.py', case, '--dry-run')
    check('B04 已打卡跳过', proc.returncode == 0 and 'SKIP' in proc.stdout, f'rc={proc.returncode}')

    reset_state(); case = make_case()
    proc = run_script('checkin.py', case, '--no-mail')
    check('B05 打卡成功', proc.returncode == 0 and any(m == 'POST' for m, _ in STATE['requests']),
          f'rc={proc.returncode}')

    reset_state(); STATE['checkin_auth_fail'] = True; STATE['logged_in'] = False; case = make_case()
    proc = run_script('checkin.py', case, '--no-mail')
    posts = [p for m, p in STATE['requests'] if 'stu_login' in p]
    check('B07 token 自愈', proc.returncode == 0 and len(posts) == 1, f'rc={proc.returncode},logins={len(posts)}')


def test_happy():
    reset_state(); STATE['happy_detail']['workDayToday'] = 'false'; case = make_case()
    proc = run_script('happy_checkin.py', case, '--dry-run')
    check('C03 字符串 false', proc.returncode == 6 and 'SKIP-NOTWORKDAY' in proc.stdout,
          f'rc={proc.returncode}')

    reset_state(); case = make_case()
    proc = run_script('happy_checkin.py', case, '--no-mail')
    posts = [p for m, p in STATE['requests'] if 'apattendance' in p and m == 'POST']
    check('C04 MAC 成功', proc.returncode == 0 and len(posts) == 1, f'rc={proc.returncode},posts={len(posts)}')

    reset_state(); STATE['happy_post'] = 'gps'; case = make_case()
    proc = run_script('happy_checkin.py', case, '--no-mail')
    methods = []
    for method, path in STATE['requests']:
        if method == 'POST' and 'apattendance' in path:
            methods.append(method)
    check('C05 MAC 失败回退 GPS', proc.returncode == 0 and len([x for x in STATE['requests'] if x[0] == 'POST' and 'apattendance' in x[1]]) == 3,
          f'rc={proc.returncode},posts={len([x for x in STATE["requests"] if x[0] == "POST" and "apattendance" in x[1]])}')


def test_budget():
    import importlib
    import happy_checkin
    case = make_case()
    _root, config, defaults, state = case
    old_env = {key: os.environ.get(key) for key in ('CONFIG_DIR', 'DEFAULTS_DIR', 'STATE_DIR')}
    os.environ.update({'CONFIG_DIR': str(config), 'DEFAULTS_DIR': str(defaults),
                       'STATE_DIR': str(state)})
    importlib.reload(happy_checkin)
    clock = SimpleNamespace(now=0.0, submits=0, fetches=0)
    happy_checkin.time = SimpleNamespace(
        monotonic=lambda: clock.now,
        sleep=lambda seconds: setattr(clock, 'now', clock.now + seconds),
    )

    def fetch_detail(token, deadline=None):
        clock.fetches += 1
        if clock.fetches > 1:
            clock.now += 5
        return token, {'studentName': 'x', 'checkInTime': '', 'workDayToday': True,
                       'canCheckIn': True, 'status': 0, 'venueName': 'v'}, None

    def submit(token, creds, sess, body, tag, deadline=None):
        clock.submits += 1
        clock.now += 25
        return token, 500, {'success': False, 'message': 'fail'}

    happy_checkin.fetch_detail = fetch_detail
    happy_checkin.submit = submit
    happy_checkin.send_mail = lambda *args, **kwargs: True
    happy_checkin.log = lambda message: None
    happy_checkin.random = SimpleNamespace(sample=lambda values, count: list(values))
    old_argv = sys.argv
    sys.argv = ['happy_checkin.py', '--no-mail']
    try:
        rc = happy_checkin.main()
    finally:
        sys.argv = old_argv
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(happy_checkin)
    check('C08 预算硬截止', rc == 3 and clock.submits == 2 and clock.now <= 55,
          f'rc={rc},submits={clock.submits},elapsed={clock.now}')


def test_portal():
    import portal_auth

    def run_portal(hosts, location, allow_any=False, schemes=('http', 'https'),
                   still_redirect=False):
        reset_state()
        STATE['portal_location'] = location
        STATE['portal_still_redirect'] = still_redirect
        case = make_case()
        _root, config, defaults, state = case
        write_json(config / 'portal.json', {
            'umac': '11:22:33:44:55:66', 'uplinkMac': 'AA:BB:CC:DD:EE:FF',
            'allowedHosts': hosts, 'allowedSchemes': list(schemes),
            'allowAny': allow_any, 'macFormat': 'auto',
        })
        portal_auth.CONFIG_DIR = str(config)
        portal_auth.DEFAULTS_DIR = str(defaults)
        portal_auth.STATE_DIR = str(state)
        portal_auth.LOG = str(state / 'logs' / 'portal-auth.log')
        portal_auth.if_ip = lambda iface: '127.0.0.1'
        portal_auth.if_mac = lambda iface: 'aa-bb-cc-dd-ee-02'
        old = sys.argv
        sys.argv = ['portal_auth.py', 'eth0']
        try:
            rc = portal_auth.main()
        finally:
            sys.argv = old
        return rc, state

    location = (f'http://127.0.0.1:{PORT}/portal?uaddress=192.0.2.5&umac=AA-BB-CC-DD-EE-01'
                '&ac-ip=192.0.2.1&ssid=%E6%95%99%E5%AD%A6%E6%A5%BC%25&pushPageId=42')
    rc, state = run_portal(['127.0.0.1'], location)
    captured = STATE['portal_captures'][0] if STATE['portal_captures'] else {}
    check('E05/E07 Portal 白名单+解码+复核',
          rc == 0 and len(STATE['portal_captures']) == 1 and captured.get('ssid') == '教学楼%'
          and captured.get('umac') == '11-22-33-44-55-66'
          and 're-auth verified' in (state / 'logs' / 'portal-auth.log').read_text(encoding='utf-8'),
          f'rc={rc},captures={len(STATE["portal_captures"])},ssid={captured.get("ssid")}')

    rc, state = run_portal(['127.0.0.1'], 'http://attacker.invalid/portal?uaddress=1.2.3.4')
    log = (state / 'logs' / 'portal-auth.log').read_text(encoding='utf-8')
    check('E05 恶意重定向拒绝', rc == 0 and not STATE['portal_captures'] and 'blocked redirect host=attacker.invalid' in log,
          f'rc={rc},captures={len(STATE["portal_captures"])}')

    rc, state = run_portal([], location)
    log = (state / 'logs' / 'portal-auth.log').read_text(encoding='utf-8')
    check('E05 空白名单 fail-closed', rc == 0 and not STATE['portal_captures'] and 'allowedHosts 为空' in log,
          f'rc={rc},captures={len(STATE["portal_captures"])}')

    rc, state = run_portal(['127.0.0.1'], location, schemes=('https',))
    log = (state / 'logs' / 'portal-auth.log').read_text(encoding='utf-8')
    check('E05 协议不允许拒绝', not STATE['portal_captures'] and 'blocked redirect scheme=http' in log,
          f'captures={len(STATE["portal_captures"])}')

    rc, state = run_portal(['127.0.0.1'], location, still_redirect=True)
    log = (state / 'logs' / 'portal-auth.log').read_text(encoding='utf-8')
    check('E06 登录后复核仍重定向', len(STATE['portal_captures']) == 1 and '仍被重定向' in log,
          f'captures={len(STATE["portal_captures"])}')


def test_scheduler():
    import scheduler

    class StopLoop(Exception):
        pass

    class FakeDateTime(datetime.datetime):
        current = datetime.datetime(2026, 10, 25, 12, 0, 0)
        @classmethod
        def now(cls, tz=None):
            return cls.current

    class Clock:
        def __init__(self, current, epoch):
            self.current = current
            self.epoch = epoch
            FakeDateTime.current = current
            self.loops = 0

        def time(self):
            return self.epoch

        def sleep(self, seconds):
            self.epoch += seconds
            self.current += datetime.timedelta(seconds=seconds)
            FakeDateTime.current = self.current
            self.loops += 1
            raise StopLoop()

    class ImmediateThread:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    cfg = {
        'checkin': {'hour': 21, 'minute': 0, 'random_delay_sec_max': 0,
                    'retry_after_min': 20, 'notify_before_min': 15},
        'happy': {'hour': 23, 'minute': 59, 'random_delay_sec_max': 0,
                  'retry_after_min': 20, 'notify_before_min': 0, 'budget_sec': 55},
        'portal': {'interval_minutes': 1}, '_warnings': [],
    }

    def run_sched(state_dir, current, epoch, rc=0, loops=1):
        clock = Clock(current, epoch)
        calls = []
        old = {k: getattr(scheduler, k) for k in (
            'load_schedule', 'spawn', 'log', 'datetime', 'time', 'threading',
            'STATE_FILE', 'LOG', 'HEARTBEAT', 'STATE_DIR')}
        scheduler.load_schedule = lambda path, required=False: cfg
        scheduler.spawn = lambda name, argv: calls.append(name) or rc
        scheduler.log = lambda message: None
        scheduler.datetime = SimpleNamespace(datetime=FakeDateTime, timedelta=datetime.timedelta)
        scheduler.time = SimpleNamespace(time=clock.time, sleep=clock.sleep)
        scheduler.threading = SimpleNamespace(Thread=ImmediateThread, RLock=old['threading'].RLock)
        scheduler.STATE_FILE = str(state_dir / 'scheduler_state.json')
        scheduler.LOG = str(state_dir / 'scheduler.log')
        scheduler.HEARTBEAT = str(state_dir / 'heartbeat')
        scheduler.STATE_DIR = str(state_dir)
        old_env = os.environ.get('RVTU_SCHEDULER_MAX_LOOPS')
        os.environ['RVTU_SCHEDULER_MAX_LOOPS'] = str(loops)
        try:
            scheduler.main()
        except StopLoop:
            pass
        finally:
            if old_env is None:
                os.environ.pop('RVTU_SCHEDULER_MAX_LOOPS', None)
            else:
                os.environ['RVTU_SCHEDULER_MAX_LOOPS'] = old_env
            for key, value in old.items():
                setattr(scheduler, key, value)
        state = json.loads((state_dir / 'scheduler_state.json').read_text(encoding='utf-8'))
        return calls, state

    sd = pathlib.Path(tempfile.mkdtemp())
    calls, state = run_sched(sd, datetime.datetime(2026, 10, 25, 20, 45), 1000000.0)
    check('F02 预检触发', 'checkin-precheck' in calls and state['prechecks'].get('checkin') is True,
          f'calls={calls}')

    sd = pathlib.Path(tempfile.mkdtemp())
    calls, state = run_sched(sd, datetime.datetime(2026, 10, 25, 21, 0), 1000000.0)
    calls2, state2 = run_sched(sd, datetime.datetime(2026, 10, 25, 21, 0), 1000000.0)
    check('F12 重启不重复', calls.count('checkin') == 1 and calls2.count('checkin') == 0,
          f'first={calls},second={calls2}')

    sd = pathlib.Path(tempfile.mkdtemp())
    calls, state = run_sched(sd, datetime.datetime(2026, 10, 25, 21, 0), 1000000.0, rc=2)
    calls2, state2 = run_sched(sd, datetime.datetime(2026, 10, 25, 21, 20), 1001200.0, rc=0)
    check('F13 重试持久化', state['tasks']['checkin']['status'] == 'retry_pending'
          and calls2.count('checkin') == 1 and state2['tasks']['checkin']['status'] == 'done',
          f'first={calls},second={calls2}')

    sd = pathlib.Path(tempfile.mkdtemp())
    calls, state = run_sched(sd, datetime.datetime(2026, 10, 25, 22, 31), 1000000.0)
    check('F08 超过 90 分钟跳过', calls.count('checkin') == 0 and state['tasks']['checkin']['status'] == 'skipped',
          f'calls={calls},state={state["tasks"]}')


def test_bootstrap_refresh():
    import bootstrap
    base = pathlib.Path(tempfile.mkdtemp())
    env = os.environ.copy()
    env.update({
        'CREDS_USER': 'fake-id', 'CREDS_PASS': 'fake-pass',
        'MACS': 'AA:BB:CC:DD:EE:01', 'LOCATION_LAT': '35.1',
        'LOCATION_LON': '119.1', 'LOCATION_INFO': 'test',
        'PORTAL_ALLOWED_HOSTS': 'portal.example',
        'STATE_DIR': str(base / 'state'),
        'ENV_CONFIG_DIR': str(base / 'state' / 'config'),
        'PYTHONIOENCODING': 'utf-8',
    })
    proc = subprocess.run([PY, str(REPO / 'app' / 'bootstrap.py')], env=env,
                          capture_output=True, text=True, encoding='utf-8', timeout=20)
    check('D08 bootstrap 生成', proc.returncode == 0 and
          (base / 'state' / 'config' / 'creds.json').exists(), f'rc={proc.returncode}')
    env2 = env.copy(); env2['CREDS_USER'] = '   '
    proc = subprocess.run([PY, str(REPO / 'app' / 'bootstrap.py')], env=env2,
                          capture_output=True, text=True, encoding='utf-8', timeout=20)
    check('D10 空白 CREDS_USER 拒绝', proc.returncode == 2, f'rc={proc.returncode}')
    env4 = env.copy(); env4['MAIL_HOST'] = 'smtp.163.com'; env4['MAIL_PORT'] = '465'
    proc = subprocess.run([PY, str(REPO / 'app' / 'bootstrap.py')], env=env4,
                          capture_output=True, text=True, encoding='utf-8', timeout=20)
    mail = json.loads((base / 'state' / 'config' / 'mail.json').read_text(encoding='utf-8'))
    check('D08 默认邮件主机不误启用', proc.returncode == 0 and mail == {},
          f'rc={proc.returncode},mail={mail}')

    case = make_case()
    root, config, defaults, state = case
    env3 = os.environ.copy()
    env3.update({'CONFIG_DIR': str(config), 'DEFAULTS_DIR': str(defaults),
                 'STATE_DIR': str(state), 'PYTHONIOENCODING': 'utf-8'})
    proc = subprocess.run([PY, str(SCRIPTS / 'refresh_creds.py'), '--cipher'],
                          env=env3, capture_output=True, text=True, encoding='utf-8', timeout=20)
    check('D07 --cipher 缺值', proc.returncode == 2 and 'Traceback' not in proc.stderr,
          f'rc={proc.returncode}')

    reset_state()
    write_json(state / 'session.json', {'token': 'fake-token'})
    proc = subprocess.run([PY, str(SCRIPTS / 'refresh_creds.py'), '--check'],
                          env=env3, capture_output=True, text=True, encoding='utf-8', timeout=20)
    check('D04 refresh --check 有效', proc.returncode == 0 and '有效' in proc.stdout,
          f'rc={proc.returncode}')

    reset_state(); STATE['checkin_auth_fail'] = True; STATE['logged_in'] = False
    proc = subprocess.run([PY, str(SCRIPTS / 'refresh_creds.py'), '--check'],
                          env=env3, capture_output=True, text=True, encoding='utf-8', timeout=20)
    check('D05 refresh --check 无效', proc.returncode == 1, f'rc={proc.returncode}')

    proc = run_script('notify.py', case, '--dry-run', '测试', '正文')
    check('D01 notify dry-run', proc.returncode == 0 and 'DRY-RUN' in proc.stdout,
          f'rc={proc.returncode}')
    proc = run_script('notify.py', case, '测试', '正文')
    check('D03 notify 无邮件配置', proc.returncode == 1 and '未配置有效邮件' in proc.stdout,
          f'rc={proc.returncode}')


def main():
    test_static()
    test_config()
    test_checkin()
    test_happy()
    test_budget()
    test_portal()
    test_scheduler()
    test_bootstrap_refresh()
    failed = [item for item in RESULTS if not item[1]]
    print(f'\n总计 {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，失败 {len(failed)}')
    if failed:
        for name, _, detail in failed:
            print(f'FAIL {name} | {detail}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
