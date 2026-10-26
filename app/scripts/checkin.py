#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""日职 宿舍考勤 自动打卡 + 邮件通知（RVTU-bot，Docker / Windows 通用）

说明：环境预检已移除 —— 是否打卡由手动开关容器决定（人在学校时开着容器）。

配置（环境变量可覆盖路径）：
    CONFIG_DIR   默认 config/（Docker 内 /config）    —— 手动填写的参数
        creds.json     user（必填：身份证号）/ pass（统一密码）
        mail.json      发信配置
    DEFAULTS_DIR 默认 defaults/（Docker 内 /defaults）  —— 程序参数
        api.json       base / module / ua_checkin
    STATE_DIR    默认 state/（Docker 内 /state）       —— 运行态（自动生成）
        session.json   token / idCardEnc / publicKeyBase64
        logs/checkin.log

用法:
    python3 checkin.py            # 打卡（幂等：已打卡则跳过），并邮件汇报
    python3 checkin.py --dry-run  # 只查询状态，不打卡、不发邮件
    python3 checkin.py --force    # 忽略已打卡状态，强制再打一次
    python3 checkin.py --no-mail  # 不发邮件（只打终端 + 写日志）
    python3 checkin.py --precheck # 打卡前预检（调度器在打卡前 15 分钟调用）

退出码: 0 成功或已打卡 / 2 凭据或接口异常 / 3 打卡未成功

通知策略（只留邮件）:
    打卡结果邮件发往 mail.json；--dry-run 静默不发。
    --precheck 预检模式：查询今日状态后，仅以下两种情况发邮件——
        待打卡 → 发"将在 21:00 自动打卡"提醒（含阻止方法）；
        预检失败（凭据/接口异常）→ 发"预检失败"提醒（打卡仍会按计划尝试）；
    已打卡 → 静默不发。
"""
import json, os, sys, smtplib, ssl, urllib.request, urllib.error, datetime
from email.message import EmailMessage

from configutil import (ConfigError, DEFAULT_CONFIG_DIR, DEFAULT_DEFAULTS_DIR,
                        DEFAULT_STATE_DIR, load_api, load_creds, load_mail,
                        load_schedule)
from logutil import append_log

CONFIG_DIR = os.environ.get('CONFIG_DIR', DEFAULT_CONFIG_DIR)
DEFAULTS_DIR = os.environ.get('DEFAULTS_DIR', DEFAULT_DEFAULTS_DIR)
STATE_DIR = os.environ.get('STATE_DIR', DEFAULT_STATE_DIR)
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

CREDS = os.path.join(CONFIG_DIR, 'creds.json')
MAILCFG = os.path.join(CONFIG_DIR, 'mail.json')
APICFG = os.path.join(DEFAULTS_DIR, 'api.json')
SCHEDCFG = os.path.join(DEFAULTS_DIR, 'schedule.json')
SESSION = os.path.join(STATE_DIR, 'session.json')
LOGDIR = os.path.join(STATE_DIR, 'logs')
LOG = os.path.join(LOGDIR, 'checkin.log')

# 以下为兜底默认，正常由 defaults/api.json 提供
DEFAULT_BASE = 'https://rzptxsgl.rzpt.cn/xsxxgl-server'
DEFAULT_UA = ('Mozilla/5.0 (Linux; Android 16; 25079RPDCC Build/BP2A.250605.031.A3; wv) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.192 Safari/537.36')
BASE = DEFAULT_BASE
UA = DEFAULT_UA
MODULE = 'sskq'

STATUS_TXT = {0: '未打卡', 1: '正常', 2: '迟到', 3: '缺勤', 4: '异常'}


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    append_log(LOG, line)


def load_json(path: str):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_session(s: dict) -> None:
    """把登录态写回 state/session.json（token / idCardEnc / publicKeyBase64）"""
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


def send_mail(mailcfg, subject: str, body: str) -> bool:
    m = mailcfg
    if not m:
        log('MAIL 跳过：未配置有效邮件')
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
    pre = '--precheck' in sys.argv

    mailcfg = None
    try:
        mailcfg = load_mail(MAILCFG)
    except ConfigError as exc:
        log(f'WARN 邮件配置无效，本次不发送邮件：{exc}')

    def notify(subject: str, body: str) -> None:
        """统一发信入口：--dry-run / --no-mail 一律不发。"""
        if dry or no_mail:
            return
        send_mail(mailcfg, subject, body)

    def nowstr() -> str:
        return f"时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"

    # 打卡时间（用于预检提醒文案），取自 defaults/schedule.json 的 checkin 段
    try:
        _c = load_schedule(SCHEDCFG)['checkin']
        hhmm = f"{_c['hour']:02d}:{_c['minute']:02d}"
    except ConfigError as exc:
        log(f'WARN 调度配置无效，使用默认 21:00：{exc}')
        hhmm = '21:00'

    def fail_mail(what: str, detail: str) -> None:
        """失败提醒：预检模式提示打卡仍会尝试；正式模式按打卡失败汇报。"""
        if pre:
            notify(f'【日职考勤】预检失败（{what}）',
                   f'{detail}\n今天 {hhmm} 仍会按计划尝试打卡（如需阻止请关闭容器）。\n{nowstr()}')
        else:
            notify(f'【日职考勤】打卡失败（{what}）', f'{detail}\n{nowstr()}')

    global BASE, UA, MODULE
    try:
        api_cfg = load_api(APICFG)
    except ConfigError as exc:
        log(f'FAIL 接口配置错误：{exc}')
        fail_mail('接口配置异常', f'接口配置错误：{exc}')
        return 2
    BASE = api_cfg['base']
    UA = api_cfg['ua_checkin']
    MODULE = api_cfg['module']

    try:
        creds = load_creds(CREDS)
    except ConfigError as exc:
        log(f'FAIL 配置错误：{exc}')
        fail_mail('凭据配置异常', f'{exc}')
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
            fail_mail('登录失效', f'无法登录打卡系统：{e}')
            return 2
        st, res = api('GET', '/mobile/attendance/records/v1/detail', token=token)
        if not (isinstance(res, dict) and res.get('success')):
            log(f'FAIL 重登后 detail 仍失败 http={st} resp={str(res)[:200]}')
            fail_mail('接口异常', f'detail 接口返回异常：http={st}\n{str(res)[:300]}')
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

    if pre:
        # ---- 预检模式：仅“待打卡”发提醒；“已打卡”静默 ----
        if r.get('checkInTime'):
            log(f"PRECHECK 今日已打卡（{r['checkInTime']}），静默不发邮件")
            return 0
        log(f'PRECHECK 今日待打卡，发送打卡前提醒（{hhmm}）')
        notify(f'【日职考勤】将在 {hhmm} 自动打卡（预检：未打卡）',
               f'预检结果：今日尚未打卡，{hhmm} 将自动执行。\n'
               f'如需阻止本次打卡，请在此之前关闭容器（docker compose stop / docker stop）。\n\n{info}\n{nowstr()}')
        return 0

    if r.get('checkInTime') and not force:
        log(f"SKIP 今日已打卡（{r['checkInTime']}），不重复提交")
        notify('【日职考勤】今日已打卡（跳过）',
               f"结论：无需打卡，系统已有记录（{r['checkInTime']}）。\n\n{info}\n\n{nowstr()}")
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
            send_mail(mailcfg, f"【日职考勤】打卡成功 {body['checkInTime']}",
                      f"结论：打卡成功。\n\n提交：日期={body['checkInDate']} 时间={body['checkInTime']} 方式=手机签到\n"
                      f"服务器返回：{res2.get('message')}\n{verify}\n\n{info}\n\n时间：{now:%Y-%m-%d %H:%M:%S}")
        return 0

    # 提交失败/超时后复核：若服务端已有今日打卡记录，视为成功（常见于请求超时但服务端已受理）
    st3, res3 = api('GET', '/mobile/attendance/records/v1/detail', token=token)
    if isinstance(res3, dict) and res3.get('success'):
        rr = res3.get('result')
        if isinstance(rr, dict) and rr.get('checkInTime'):
            log(f"OK 复核发现今日已打卡（{rr.get('checkInTime')}），判定本次成功（原提交 http={st} resp={str(res2)[:120]}）")
            if not no_mail:
                send_mail(mailcfg, f"【日职考勤】打卡成功 {rr.get('checkInTime')}（复核确认）",
                          f"结论：打卡成功（原提交响应异常，复核确认已有打卡记录）。\n\n"
                          f"复核：时间={rr.get('checkInTime')}｜状态={STATUS_TXT.get(rr.get('status'), rr.get('status'))}\n"
                          f"原响应（http={st}）：{str(res2)[:300]}\n\n{info}\n\n时间：{now:%Y-%m-%d %H:%M:%S}")
            return 0

    log(f"FAIL 打卡未成功 http={st} resp={str(res2)[:200]}")
    notify(f"【日职考勤】打卡失败 {body['checkInTime']}",
           f"结论：打卡未成功，请手动打开 App 确认。\n\n提交：{json.dumps(body, ensure_ascii=False)}\n"
           f"返回（http={st}）：{str(res2)[:400]}\n\n{info}\n\n时间：{now:%Y-%m-%d %H:%M:%S}")
    return 3


if __name__ == '__main__':
    sys.exit(main())
