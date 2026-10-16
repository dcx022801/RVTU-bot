#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""乐享晚自习 自动打卡（容器版，rzptxsgl.rzpt.cn / xsxxgl-server / apattendance）

配置分三层（环境变量可覆盖路径）：
    CONFIG_DIR   默认 /config    —— 手动填写的参数
        creds.json     user（必填：身份证号）/ pass（统一密码）
        macs.json      macs[]（必填：MAC 候选池，随机顺序逐个尝试）
        location.json  latitude / longitude / locationInfo（必填：定位通道备用）
        mail.json      发信配置
    DEFAULTS_DIR 默认 /defaults  —— 程序参数
        api.json       base / module / ua_happy
    STATE_DIR    默认 /state     —— 运行态（自动生成）
        session.json       token / idCardEnc / publicKeyBase64
        logs/happy_checkin.log

用法:
    python3 happy_checkin.py            # 打卡（幂等：已打卡则跳过）
    python3 happy_checkin.py --dry-run  # 只查询状态，不打卡、不发邮件
    python3 happy_checkin.py --force    # 忽略已打卡状态，强制再打一次
    python3 happy_checkin.py --no-mail  # 不发邮件（只打终端 + 写日志）
    python3 happy_checkin.py --precheck # 打卡前预检（调度器在打卡前 15 分钟调用）

退出码: 0 成功或已打卡 / 2 凭据或配置异常 / 3 打卡未成功 / 6 今日非上课日（静默）

通知策略（只留邮件）:
    打卡结果邮件发往 mail.json；非上课日(rc=6) 与 --dry-run 静默不发。
    --precheck 预检模式：查询今日状态后，仅以下两种情况发邮件——
        待打卡 → 发"将在 19:00 自动打卡"提醒（含阻止方法）；
        预检失败（凭据/接口/配置异常）→ 发"预检失败"提醒（打卡仍会按计划尝试）；
    已打卡、非上课日 → 静默不发。

打卡协议:
    GET  /mobile/apattendance/checkin-record/v1/detail        今日状态
    POST /mobile/apattendance/checkin-record/v1/check/in
     ① MAC 通道   {macAddress:<macs.json 随机顺序逐个>, checkInMethod:1}
        服务端按 MAC 白名单查表（静态表，不校验 AP 在线），第一个成功即停；全失败才走 ②
     ② 定位通道   {macAddress, checkInMethod:2, latitude, longitude, locationInfo}
        GPS 700m 圈判定，不查 MAC。①返回成功但复核无打卡时间时自动回退到②
    窗口 19:00 开 / 19:20 迟到线 / 19:40 关；workDays "1,2,3,4,7" = 周日+周一~周四。
"""
import json, os, sys, random, datetime, smtplib, ssl
import urllib.request, urllib.error
from email.message import EmailMessage

CONFIG_DIR = os.environ.get('CONFIG_DIR', '/config')
DEFAULTS_DIR = os.environ.get('DEFAULTS_DIR', '/defaults')
STATE_DIR = os.environ.get('STATE_DIR', '/state')
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

CREDS = os.path.join(CONFIG_DIR, 'creds.json')
MACSCFG = os.path.join(CONFIG_DIR, 'macs.json')
LOCCFG = os.path.join(CONFIG_DIR, 'location.json')
MAILCFG = os.path.join(CONFIG_DIR, 'mail.json')
APICFG = os.path.join(DEFAULTS_DIR, 'api.json')
SCHEDCFG = os.path.join(DEFAULTS_DIR, 'schedule.json')
SESSION = os.path.join(STATE_DIR, 'session.json')
LOGDIR = os.path.join(STATE_DIR, 'logs')
LOG = os.path.join(LOGDIR, 'happy_checkin.log')

# 以下为兜底默认，正常由 /defaults/api.json 提供
DEFAULT_BASE = 'https://rzptxsgl.rzpt.cn/xsxxgl-server'
DEFAULT_UA = ('Mozilla/5.0 (Linux; Android 16; PLK110 Build/BP2A.250605.015; wv) '
              'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/154.0.8037.106 Mobile Safari/537.36')
BASE = DEFAULT_BASE
UA = DEFAULT_UA
MODULE = 'sskq'

STATUS_TXT = {0: '未打卡', 1: '正常', 2: '迟到', 3: '缺勤', 4: '异常'}
DETAIL = '/mobile/apattendance/checkin-record/v1/detail'
CHECKIN = '/mobile/apattendance/checkin-record/v1/check/in'


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
    """把登录态写回 /state/session.json"""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SESSION, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(SESSION, 0o600)
    except Exception:
        pass


def send_mail(subject: str, body: str) -> bool:
    """发结果邮件；配置缺失或发送失败都只记日志，不影响打卡结果。"""
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


def api(method: str, path: str, token: str = None, body: dict = None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header('User-Agent', UA)
    r.add_header('referer', 'https://rzptxsgl.rzpt.cn/ydxsxxgl/')
    r.add_header('X-Requested-With', 'com.sdzxkj.wisdom')
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
    """用 creds.json 的 user（身份证号）重新登录，新 token 写回 session"""
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


def _dead(r) -> bool:
    m = str(r.get('message', '')) if isinstance(r, dict) else ''
    return ('失效' in m) or ('重新登录' in m)


def fetch_detail(token: str):
    st, res = api('GET', DETAIL, token=token)
    if isinstance(res, dict) and res.get('success') and isinstance(res.get('result'), dict):
        return token, res['result'], None
    return token, None, f'http={st} resp={str(res)[:200]}'


def submit(token: str, creds: dict, sess: dict, body: dict, tag: str):
    """POST check/in；遇 token 失效自动重登后重试一次。返回 (token, http状态, 响应)"""
    st, res = api('POST', CHECKIN, token=token, body=body)
    if _dead(res):
        log(f"WARN {tag} token 失效：{res.get('message')}，重新登录后重试一次…")
        try:
            token = relogin(creds, sess)
            st, res = api('POST', CHECKIN, token=token, body=body)
        except Exception as e:
            log(f'FAIL {tag} 重登录失败：{type(e).__name__}: {e}')
    return token, st, res


def main() -> int:
    dry = '--dry-run' in sys.argv
    force = '--force' in sys.argv
    no_mail = '--no-mail' in sys.argv
    pre = '--precheck' in sys.argv

    def notify(subject: str, body: str) -> None:
        """统一发信入口：--dry-run / --no-mail 一律不发。"""
        if dry or no_mail:
            return
        send_mail(subject, body)

    def stamp() -> str:
        return f'时间：{datetime.datetime.now():%Y-%m-%d %H:%M:%S}'

    # 打卡时间（用于预检提醒文案），取自 defaults/schedule.json 的 happy 段
    try:
        _h = (load_json(SCHEDCFG) or {}).get('happy') or {}
        hhmm = f"{int(_h.get('hour', 19)):02d}:{int(_h.get('minute', 0)):02d}"
    except Exception:
        hhmm = '19:00'

    def fail_mail(what: str, detail: str) -> None:
        """失败提醒：预检模式提示打卡仍会尝试；正式模式按打卡失败汇报。"""
        if pre:
            notify(f'【日职晚自习】预检失败（{what}）',
                   f'{detail}\n今天 {hhmm} 仍会按计划尝试打卡（如需阻止请关闭容器）。\n{stamp()}')
        else:
            notify(f'【日职晚自习】打卡失败（{what}）', f'{detail}\n{stamp()}')

    global BASE, UA, MODULE
    api_cfg = load_json(APICFG) or {}
    if api_cfg.get('base'):
        BASE = str(api_cfg['base'])
    if api_cfg.get('ua_happy'):
        UA = str(api_cfg['ua_happy'])
    if api_cfg.get('module'):
        MODULE = str(api_cfg['module'])

    creds = load_json(CREDS)
    if not creds:
        log('FAIL creds.json 读取失败')
        fail_mail('凭据文件异常', 'creds.json 读取失败，无法登录打卡系统。')
        return 2

    macs_cfg = load_json(MACSCFG) or {}
    mac_list = [str(m).strip() for m in (macs_cfg.get('macs') or []) if str(m).strip()]
    if not mac_list:
        log('FAIL macs.json 缺失或 macs 列表为空')
        fail_mail('MAC 池未配置', 'macs.json 缺失或 macs 列表为空，无法执行 MAC 通道打卡。')
        return 2

    loc = load_json(LOCCFG) or {}
    lat = str(loc.get('latitude') or '').strip()
    lon = str(loc.get('longitude') or '').strip()
    loc_info = str(loc.get('locationInfo') or '').strip()
    if not (lat and lon):
        log('FAIL location.json 缺失或坐标为空')
        fail_mail('坐标未配置', 'location.json 缺失或坐标为空，无法执行定位通道打卡。')
        return 2

    def mac_body(mac: str) -> dict:
        return {'macAddress': mac, 'checkInMethod': 1}

    def gps_body(mac: str) -> dict:
        return {'macAddress': mac, 'checkInMethod': 2,
                'latitude': lat, 'longitude': lon, 'locationInfo': loc_info}

    sess = load_json(SESSION) or {}
    token = sess.get('token') or ''

    token, r, err = fetch_detail(token)
    if err:
        log(f'detail 失败 {err}，尝试重新登录…')
        try:
            token = relogin(creds, sess)
        except Exception as e:
            log(f'FAIL {e}')
            fail_mail('登录失效', f'无法登录晚自习打卡系统：{e}')
            return 2
        token, r, err = fetch_detail(token)
        if err:
            log(f'FAIL 重登后 detail 仍失败 {err}')
            fail_mail('接口异常', f'重登后 detail 接口仍异常：{err}')
            return 2

    info = (f"姓名={r.get('studentName')}｜今日打卡={r.get('checkInTime') or '无'}｜"
            f"状态={STATUS_TXT.get(r.get('status'), r.get('status'))}｜"
            f"上课日={r.get('workDayToday')}｜可打卡={r.get('canCheckIn')}｜"
            f"打卡点={r.get('venueName')}｜窗口 {r.get('normalCheckinStart')}-{r.get('normalCheckinEnd')}-{r.get('lateCheckinDeadline')}")
    log(f'状态: {info}')

    if pre:
        # ---- 预检模式：仅“待打卡”发提醒；“已打卡/非上课日”静默 ----
        if not r.get('workDayToday'):
            log(f"PRECHECK 今日非晚自习上课日（workDays={r.get('workDays')}），静默不发邮件")
            return 6
        if r.get('checkInTime'):
            log(f"PRECHECK 今日已打卡（{r['checkInTime']}），静默不发邮件")
            return 0
        log(f'PRECHECK 今日待打卡，发送打卡前提醒（{hhmm}）')
        notify(f'【日职晚自习】将在 {hhmm} 自动打卡（预检：未打卡）',
               f'预检结果：今日需打卡且尚未打卡，{hhmm} 将自动执行。\n'
               f'如需阻止本次打卡，请在此之前关闭容器（docker compose stop / docker stop）。\n\n{info}\n{stamp()}')
        return 0

    if not r.get('workDayToday'):
        # 非上课日：完全静默（不发邮件，对齐原微信汇报的 rc=6 静默逻辑）
        log(f"SKIP-NOTWORKDAY 今日非晚自习上课日（workDays={r.get('workDays')}），不打卡、不发邮件")
        return 6

    if r.get('checkInTime') and not force:
        log(f"SKIP 今日已打卡（{r['checkInTime']}），不重复提交")
        notify(f"【日职晚自习】今日已打卡（跳过 {r['checkInTime']}）",
               f'今日已有晚自习打卡记录，未重复提交。\n{info}\n{stamp()}')
        return 0

    order = random.sample(mac_list, len(mac_list))
    if dry:
        log(f"DRY-RUN 本次随机顺序共{len(order)}个候选：{' '.join(order)}"
            f"｜②备用定位通道 {json.dumps(gps_body(order[0]), ensure_ascii=False)}")
        return 0

    fail = ''
    log(f'随机顺序（共{len(order)}个候选）：' + ' '.join(order))
    attempts = [(f'MAC通道/{m}', mac_body(m)) for m in order]
    attempts.append(('定位通道/备用', gps_body(order[0])))
    for tag, body in attempts:
        token, st, res2 = submit(token, creds, sess, body, tag)
        if isinstance(res2, dict) and res2.get('success') and not _dead(res2):
            _tok, rr, _err = fetch_detail(token)
            if rr and rr.get('checkInTime'):
                log(f"OK {tag} 打卡成功 -> {res2.get('message')}｜复核：时间={rr.get('checkInTime')}"
                    f"｜状态={STATUS_TXT.get(rr.get('status'), rr.get('status'))}｜打卡点={rr.get('venueName')}")
                notify(f"【日职晚自习】打卡成功 {rr.get('checkInTime')}",
                       f"乐享晚自习打卡成功。\n通道：{tag}｜MAC：{body.get('macAddress')}｜服务端消息：{res2.get('message')}\n"
                       f"复核：时间={rr.get('checkInTime')}｜"
                       f"状态={STATUS_TXT.get(rr.get('status'), rr.get('status'))}｜"
                       f"打卡点={rr.get('venueName')}\n{stamp()}")
                return 0
            log(f"WARN {tag} 接口报成功但复核无打卡时间，换下一条通道重试")
        else:
            log(f"WARN {tag} 未成功 http={st} resp={str(res2)[:200]}")
        fail = f'{tag} http={st} resp={str(res2)[:200]}'

    log(f"FAIL 两条通道都没打上（最后一条 {fail}）")
    notify('【日职晚自习】打卡失败',
           f'两条通道都没打上，需要人工处理（可手动在 App 打卡）。\n最后错误：{fail}\n'
           f'当时服务端状态：{info}\n{stamp()}')
    return 3


if __name__ == '__main__':
    sys.exit(main())
