#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日职 宿舍考勤 自动打卡 + 邮件通知（RVTU-bot 容器版，rzptxsgl.rzpt.cn / xsxxgl-server）

说明：环境预检已移除 —— 是否打卡由手动开关容器决定（人在学校时开着容器）。

配置（环境变量可覆盖路径）：
    CONFIG_DIR   默认 /config    —— 手动填写的参数
        creds.json     user（必填：身份证号）/ pass（统一密码）
        mail.json      发信配置
    DEFAULTS_DIR 默认 /defaults  —— 程序参数
        api.json       base / module / ua_checkin
    STATE_DIR    默认 /state     —— 运行态（自动生成）
        session.json   token / idCardEnc / publicKeyBase64
        logs/checkin.log

用法:
    python3 checkin.py            # 打卡（幂等：已打卡则跳过），并邮件汇报
    python3 checkin.py --dry-run  # 只查询状态，不打卡、不发邮件
    python3 checkin.py --force    # 忽略已打卡状态，强制再打一次
    python3 checkin.py --no-mail  # 不发邮件（只打终端 + 写日志）

退出码: 0 成功或已打卡 / 2 凭据或接口异常 / 3 打卡未成功
"""
import json, os, sys, smtplib, ssl, urllib.request, urllib.error, datetime
from email.message import EmailMessage

CONFIG_DIR = os.environ.get('CONFIG_DIR', '/config')
DEFAULTS_DIR = os.environ.get('DEFAULTS_DIR', '/defaults')
STATE_DIR = os.environ.get('STATE_DIR', '/state')
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

CREDS = os.path.join(CONFIG_DIR, 'creds.json')
MAILCFG = os.path.join(CONFIG_DIR, 'mail.json')
APICFG = os.path.join(DEFAULTS_DIR, 'api.json')
SESSION = os.path.join(STATE_DIR, 'session.json')
LOGDIR = os.path.join(STATE_DIR, 'logs')
LOG = os.path.join(LOGDIR, 'checkin.log')

# 以下为兜底默认，正常由 /defaults/api.json 提供
DEFAULT_BASE = 'https://rzptxsgl.rzpt.cn/xsxxgl-server'
DEFAULT_UA = ('Mozilla/5.0 (Linux; Android 16; 25079RPDCC Build/BP2A.250605.031.A3; wv) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.192 Safari/537.36')
BASE = DEFAULT_BASE
UA = DEFAULT_UA
MODULE = 'sskq'

STATUS_TXT = {0: '未打卡', 1: '正常', 2: '迟到', 3: '缺勤', 4: '异常'}


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def load_json(path: str):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_session(s: dict) -> None:
    """把登录态写回 /state/session.json（token / idCardEnc / publicKeyBase64）"""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SESSION, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(SESSION, 0o600)
    except Exception:
        pass


def api(method: str, path: str, token: str = None, body: dict = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header('User-Agent', UA)
    r.add_header('referer', 'https://rzptxsgl.rzpt.cn/ydxsxxgl/')
    if token:
        r.add_header('x-access-token', token)
    if data:
        r.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            raw = resp.read().decode('utf-8', 'ignore')
            try:
                return resp.status, json.loads(raw)
            except Exception:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', 'ignore')
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return -1, str(e)


def relogin(creds: dict, sess: dict) -> str:
    """用 creds.json 的 user（身份证号）重新登录（自愈：拉最新公钥 → 本地加密 → 登录 → 写回 session）"""
    pub, enc = sess.get('publicKeyBase64'), sess.get('idCardEnc')
    plain = creds.get('user')
    if plain:
        st0, r0 = api('GET', '/mobile_auth/public_key')
        if isinstance(r0, dict) and r0.get('result'):
            try:
                if SCRIPTS_DIR not in sys.path:
                    sys.path.insert(0, SCRIPTS_DIR)
                from refresh_creds import rsa_encrypt
                pub = r0['result']
                enc = rsa_encrypt(pub, plain)
                log('relogin: 已用服务端当前公钥重新加密身份信息')
            except Exception as e:
                log(f'WARN relogin 本地加密失败，回退旧密文重放：{type(e).__name__}: {e}')
    st, res = api('POST', '/mobile_auth/stu_login', body={
        'idCard': enc,
        'module': MODULE,
        'publicKeyBase64': pub,
    })
    if isinstance(res, dict) and res.get('success') and (res.get('result') or {}).get('token'):
        sess['token'] = res['result']['token']
        sess['publicKeyBase64'] = pub
        sess['idCardEnc'] = enc
        save_session(sess)
        log(f"重新登录成功（新 token 长度 {len(sess['token'])}）")
        return sess['token']
    raise RuntimeError(f'重新登录失败 http={st} resp={str(res)[:200]}')


def send_mail(subject: str, body: str) -> bool:
    m = load_json(MAILCFG)
    if not m:
        log('MAIL 跳过：没有可用的 mail.json')
        return False
    msg = EmailMessage()
    msg['From'] = m['sender']
    msg['To'] = ', '.join(m['to'])
    msg['Subject'] = subject
    msg.set_content(body)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(m['host'], m['port'], context=ctx, timeout=30) as s:
            s.login(m['user'], m['password'])
            s.send_message(msg)
        log(f"MAIL 已发送 -> {', '.join(m['to'])} | 主题: {subject}")
        return True
    except Exception as e:
        log(f'MAIL 发送失败: {type(e).__name__}: {e}')
        return False


def main() -> int:
    dry = '--dry-run' in sys.argv
    force = '--force' in sys.argv
    no_mail = '--no-mail' in sys.argv

    global BASE, UA, MODULE
    api_cfg = load_json(APICFG) or {}
    if api_cfg.get('base'):
        BASE = str(api_cfg['base'])
    if api_cfg.get('ua_checkin'):
        UA = str(api_cfg['ua_checkin'])
    if api_cfg.get('module'):
        MODULE = str(api_cfg['module'])

    creds = load_json(CREDS)
    if not creds:
        log('FAIL creds.json 读取失败')
        return 2
    sess = load_json(SESSION) or {}
    token = sess.get('token') or ''

    st, res = api('GET', '/mobile/attendance/records/v1/detail', token=token)
    if not (isinstance(res, dict) and res.get('success')):
        log(f"detail 失败 http={st}，尝试重新登录… resp={str(res)[:150]}")
        try:
            token = relogin(creds, sess)
        except Exception as e:
            log(f'FAIL {e}')
            if not no_mail:
                send_mail('【日职考勤】打卡失败（登录失效）', f'无法登录打卡系统：{e}\n时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}')
            return 2
        st, res = api('GET', '/mobile/attendance/records/v1/detail', token=token)
        if not (isinstance(res, dict) and res.get('success')):
            log(f'FAIL 重登后 detail 仍失败 http={st} resp={str(res)[:200]}')
            if not no_mail:
                send_mail('【日职考勤】打卡失败（接口异常）', f'detail 接口返回异常：http={st}\n{str(res)[:300]}')
            return 2

    r = res.get('result')
    if not isinstance(r, dict):
        log(f"WARN detail 返回 result 为空（{str(r)[:80]}），尝试重新登录后复查…")
        try:
            token = relogin(creds, sess)
            st, res = api('GET', '/mobile/attendance/records/v1/detail', token=token)
            if isinstance(res, dict) and res.get('success'):
                r = res.get('result')
        except Exception as e:
            log(f'WARN 重新登录失败（{type(e).__name__}: {e}），按“今日暂无记录”继续打卡')
    if not isinstance(r, dict):
        log('WARN result 仍为空，按“今日暂无打卡记录”继续打卡')
        r = {}
    if r:
        info = (f"姓名={r.get('studentName')}｜考勤组={r.get('groupName')}｜"
                f"今日打卡={r.get('checkInTime') or '无'}｜状态={STATUS_TXT.get(r.get('status'), r.get('status'))}｜"
                f"窗口 {r.get('openTime')}-{r.get('lateTime')}-{r.get('closeTime')}｜半径{r.get('signDistance')}m")
    else:
        info = '今日暂无打卡记录（detail.result 为空）'
    log(f'状态: {info}')

    if r.get('checkInTime') and not force:
        log(f"SKIP 今日已打卡（{r['checkInTime']}），不重复提交")
        if dry or no_mail:
            return 0
        send_mail('【日职考勤】今日已打卡（跳过）',
                  f"结论：无需打卡，系统已有记录（{r['checkInTime']}）。\n\n{info}\n\n时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
        return 0

    now = datetime.datetime.now()
    body = {'checkInDate': now.strftime('%Y-%m-%d'), 'checkInMethod': 1,
            'checkInTime': now.strftime('%H:%M:%S'), 'status': 1}
    if dry:
        log(f"DRY-RUN 将 POST check/in body={json.dumps(body, ensure_ascii=False)}")
        return 0

    st, res2 = api('POST', '/mobile/attendance/records/v1/check/in', token=token, body=body)

    def _dead(r):
        m = str(r.get('message', '')) if isinstance(r, dict) else ''
        return ('失效' in m) or ('重新登录' in m)

    if _dead(res2):
        log(f"WARN check/in 遭 token 失效：{res2.get('message')}，重新登录后重试一次…")
        try:
            token = relogin(creds, sess)
            st, res2 = api('POST', '/mobile/attendance/records/v1/check/in', token=token, body=body)
        except Exception as e:
            log(f'FAIL 重登录失败：{type(e).__name__}: {e}')
    if isinstance(res2, dict) and res2.get('success') and not _dead(res2):
        log(f"OK 打卡成功 {json.dumps(body, ensure_ascii=False)} -> {res2.get('message')}")
        verify = ''
        st3, res3 = api('GET', '/mobile/attendance/records/v1/detail', token=token)
        if isinstance(res3, dict) and res3.get('success'):
            rr = res3.get('result')
            if not isinstance(rr, dict):
                rr = {}
            if rr:
                verify = (f"复核：打卡时间={rr.get('checkInTime')}｜状态={STATUS_TXT.get(rr.get('status'), rr.get('status'))}")
            else:
                verify = '复核：接口未返回记录（result 为空）'
            log(verify)
        if not no_mail:
            send_mail(f"【日职考勤】打卡成功 {body['checkInTime']}",
                      f"结论：打卡成功。\n\n提交：日期={body['checkInDate']} 时间={body['checkInTime']} 方式=手机签到\n"
                      f"服务器返回：{res2.get('message')}\n{verify}\n\n{info}\n\n时间：{now:%Y-%m-%d %H:%M:%S}")
        return 0

    log(f"FAIL 打卡未成功 http={st} resp={str(res2)[:200]}")
    if not no_mail:
        send_mail(f"【日职考勤】打卡失败 {body['checkInTime']}",
                  f"结论：打卡未成功，请手动打开 App 确认。\n\n提交：{json.dumps(body, ensure_ascii=False)}\n"
                  f"返回（http={st}）：{str(res2)[:400]}\n\n{info}\n\n时间：{now:%Y-%m-%d %H:%M:%S}")
    return 3


if __name__ == '__main__':
    sys.exit(main())
