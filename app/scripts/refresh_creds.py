#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""刷新考勤系统凭据（Docker / Windows 通用）

背景：服务端会轮换 RSA 公钥。公钥一变，旧 token 会失效、旧 idCard 密文也登录不上
（报「身份认证密钥失败，请刷新后重试！」）。

配置：
    config/creds.json（Docker 内 /config/creds.json）     user（身份证号；命令行不给参数时默认用它）
    defaults/api.json（Docker 内 /defaults/api.json）     base（接口地址）
    state/session.json（Docker 内 /state/session.json）    登录态（token / idCardEnc / publicKeyBase64，运行自动更新）

用法：
    python3 refresh_creds.py --check          # 只检查当前 token 是否还有效
    python3 refresh_creds.py                  # 用 creds.json 里的 user（身份证号）登录
    python3 refresh_creds.py <身份证号>        # 用指定身份证号登录（自动拉最新公钥 -> 加密 -> 登录 -> 落盘）
"""
import argparse, base64, json, os, secrets, sys, urllib.request, urllib.error

from configutil import (ConfigError, DEFAULT_CONFIG_DIR, DEFAULT_DEFAULTS_DIR,
                        DEFAULT_STATE_DIR, load_api, load_creds, require_text)

CONFIG_DIR = os.environ.get('CONFIG_DIR', DEFAULT_CONFIG_DIR)
DEFAULTS_DIR = os.environ.get('DEFAULTS_DIR', DEFAULT_DEFAULTS_DIR)
STATE_DIR = os.environ.get('STATE_DIR', DEFAULT_STATE_DIR)

CREDS = os.path.join(CONFIG_DIR, 'creds.json')
APICFG = os.path.join(DEFAULTS_DIR, 'api.json')
SESSION = os.path.join(STATE_DIR, 'session.json')

BASE = 'https://rzptxsgl.rzpt.cn/xsxxgl-server'
UA = ('Mozilla/5.0 (Linux; Android 16; 25079RPDCC Build; wv) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.192 Safari/537.36')

MODULE = 'sskq'


def _load_api():
    """从 defaults/api.json 覆盖 BASE / UA / MODULE"""
    global BASE, UA, MODULE
    cfg = load_api(APICFG)
    BASE = cfg['base']
    UA = cfg['ua_checkin']
    MODULE = cfg['module']


def call(method, path, token=None, body=None):
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
        return -1, f'{type(e).__name__}: {e}'


def _tlv(b, i):
    tag = b[i]; i += 1
    ln = b[i]; i += 1
    if ln & 0x80:
        n = ln & 0x7f
        ln = int.from_bytes(b[i:i + n], 'big'); i += n
    return tag, b[i:i + ln], i + ln


def parse_pubkey(b64):
    """从 SubjectPublicKeyInfo(base64) 解析出 (n, e)，纯 stdlib，无需 crypto 库。"""
    der = base64.b64decode(b64)
    _, outer, _ = _tlv(der, 0)
    _, _, i = _tlv(outer, 0)              # AlgorithmIdentifier
    _, bitstr, _ = _tlv(outer, i)         # BIT STRING
    _, seq, _ = _tlv(bitstr[1:], 0)       # 跳过 unused-bits
    _, n_raw, i2 = _tlv(seq, 0)
    _, e_raw, _ = _tlv(seq, i2)
    return int.from_bytes(n_raw, 'big'), int.from_bytes(e_raw, 'big')


def rsa_encrypt(pub_b64, plain_text):
    """RSAES-PKCS1-v1_5 加密，返回 base64（与 App jsencrypt 行为一致）。"""
    n, e = parse_pubkey(pub_b64)
    k = (n.bit_length() + 7) // 8
    msg = plain_text.encode()
    if len(msg) > k - 11:
        raise ValueError(f'明文过长（{len(msg)} > {k - 11}）')
    pad = bytearray()
    while len(pad) < k - len(msg) - 3:
        pad += bytes(c for c in secrets.token_bytes(k - len(msg) - 3 - len(pad)) if c)
    em = b'\x00\x02' + bytes(pad[:k - len(msg) - 3]) + b'\x00' + msg
    return base64.b64encode(pow(int.from_bytes(em, 'big'), e, n).to_bytes(k, 'big')).decode()


def load_json(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def save_session(s):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SESSION, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(SESSION, 0o600)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description='刷新或检查 RVTU-bot 登录凭据')
    parser.add_argument('user', nargs='?', help='身份证号（省略时使用 creds.json）')
    parser.add_argument('--check', action='store_true', help='只检查当前 token 是否有效')
    parser.add_argument('--cipher', help='外部提供的 idCard 密文（需带值）')
    args = parser.parse_args()

    if args.cipher is not None and not args.cipher.strip():
        parser.error('--cipher 不能为空')

    try:
        _load_api()
    except ConfigError as exc:
        print(f'FAIL 接口配置错误：{exc}')
        return 2
    sess = load_json(SESSION) or {}

    if args.check:
        st, res = call('GET', '/mobile/attendance/records/v1/detail', token=sess.get('token'))
        ok = isinstance(res, dict) and res.get('success') and isinstance(res.get('result'), dict)
        print(f'token 有效性: {"有效" if ok else "无效/失效"} (http={st})')
        if not ok:
            message = res.get('message') if isinstance(res, dict) else str(res)[:160]
            print(f'响应: {message}')
        return 0 if ok else 1

    cipher = args.cipher.strip() if args.cipher else None
    if not args.user and not cipher:
        try:
            creds = load_creds(CREDS)
        except ConfigError as exc:
            print(f'FAIL 配置错误：{exc}')
            return 2
        plain = creds['user']
    else:
        try:
            plain = require_text(args.user, '身份证号') if args.user else ''
        except ConfigError as exc:
            print(f'FAIL {exc}')
            return 2

    st, res = call('GET', '/mobile_auth/public_key')
    pub = (res or {}).get('result') if isinstance(res, dict) else None
    if not pub:
        print(f'FAIL 获取公钥失败 http={st} {str(res)[:200]}')
        return 2
    changed = pub != sess.get('publicKeyBase64')
    print(f'公钥获取成功 len={len(pub)} {"（与旧版不同，已轮换）" if changed else "（与旧版相同）"}')

    if cipher:
        enc = cipher
        print(f'使用外部提供的 idCard 密文 len={len(enc)}')
    else:
        try:
            enc = rsa_encrypt(pub, plain)
        except Exception as exc:
            print(f'FAIL 身份信息加密失败：{type(exc).__name__}: {exc}')
            return 2
        print(f'账号已加密 len={len(enc)}')

    st, res = call('POST', '/mobile_auth/stu_login', body={
        'idCard': enc, 'module': MODULE, 'publicKeyBase64': pub})
    tok = (res or {}).get('result', {}) if isinstance(res, dict) else {}
    tok = (tok or {}).get('token')
    if not tok:
        print(f'FAIL 登录失败 http={st} {str(res)[:300]}')
        return 3

    sess['token'] = tok
    sess['idCardEnc'] = enc
    sess['publicKeyBase64'] = pub
    save_session(sess)
    print(f'OK 登录成功，token 已更新（长度 {len(tok)}）')

    st, res = call('GET', '/mobile/attendance/records/v1/detail', token=tok)
    r = (res or {}).get('result') if isinstance(res, dict) else None
    print('复核 detail:', (f"姓名={r.get('studentName')} 今日打卡={r.get('checkInTime') or '无'} "
                        f"窗口 {r.get('openTime')}-{r.get('closeTime')}") if isinstance(r, dict) else str(res)[:200])
    return 0


if __name__ == '__main__':
    sys.exit(main())
