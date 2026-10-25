#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .env 注入的环境变量合成为 config/*.json（仅 .env 模式启用时由 entrypoint.sh 调用）

变量命名对照（详见仓库根目录 .env.example）：
    CREDS_USER / CREDS_PASS                 登录凭据（身份证号 / 统一密码）
    MAIL_HOST / MAIL_PORT / MAIL_USER /     邮件通知
    MAIL_PASSWORD / MAIL_SENDER / MAIL_TO
    MACS                                    晚自习 MAC 池（逗号/空白分隔）
    LOCATION_LAT / LOCATION_LON /           定位通道坐标
    LOCATION_INFO
    PORTAL_UMAC / PORTAL_UPLINK_MAC         校园网认证两个 MAC（可留空）
    PORTAL_ALLOWED_HOSTS / PORTAL_ALLOW_ANY 校园网认证目标主机白名单
    PORTAL_ALLOWED_SCHEMES / PORTAL_MAC_FORMAT
"""
import json
import os
import re
import sys

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from configutil import (ConfigError, as_bool, is_placeholder, require_float,
                        require_int, require_mac, require_text)

STATE_DIR = os.environ.get('STATE_DIR', '/state')
OUT_DIR = os.environ.get('ENV_CONFIG_DIR') or os.path.join(STATE_DIR, 'config')


def env(name: str, default: str = '') -> str:
    return str(os.environ.get(name, default) or '').strip()


def split_list(value: str):
    """逗号/分号/空白分隔 → 列表（用于 MACS、MAIL_TO、白名单）"""
    return [x.strip() for x in re.split(r'[,\s;]+', value or '') if x.strip()]


def write(name: str, obj: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True, mode=0o700)
    try:
        os.chmod(OUT_DIR, 0o700)
    except Exception:
        pass
    path = os.path.join(OUT_DIR, name)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=1)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def mail_config() -> dict:
    host = env('MAIL_HOST')
    port_raw = env('MAIL_PORT')
    user = '' if is_placeholder(env('MAIL_USER')) else env('MAIL_USER')
    password = '' if is_placeholder(env('MAIL_PASSWORD')) else env('MAIL_PASSWORD')
    sender = '' if is_placeholder(env('MAIL_SENDER')) else env('MAIL_SENDER')
    recipients = [x for x in split_list(env('MAIL_TO')) if not is_placeholder(x)]
    if not any((user, password, sender, recipients)):
        return {}
    if not all((host, user, password, sender, recipients)) or is_placeholder(host):
        raise ConfigError('邮件配置不完整：MAIL_HOST/PORT/USER/PASSWORD/SENDER/TO 必须同时填写')
    port = require_int(port_raw or 465, 'MAIL_PORT', 1, 65535)
    return {'host': host, 'port': port, 'user': user, 'password': password,
            'sender': sender, 'to': recipients}


def macs_config() -> list:
    macs = split_list(env('MACS'))
    if not macs:
        raise ConfigError('MACS 未配置')
    return [require_mac(mac, 'MACS[]') for mac in macs]


def location_config() -> dict:
    _lat, lat = require_float(env('LOCATION_LAT'), 'LOCATION_LAT', -90, 90)
    _lon, lon = require_float(env('LOCATION_LON'), 'LOCATION_LON', -180, 180)
    info = require_text(env('LOCATION_INFO'), 'LOCATION_INFO')
    return {'latitude': lat, 'longitude': lon, 'locationInfo': info}


def portal_config() -> dict:
    hosts = []
    for host in split_list(env('PORTAL_ALLOWED_HOSTS')):
        host = host.lower().rstrip('.')
        if host == '*':
            raise ConfigError('PORTAL_ALLOWED_HOSTS 不允许通配符 *')
        if '://' in host or '/' in host or '@' in host:
            raise ConfigError(f'PORTAL_ALLOWED_HOSTS 只能是主机名或 IP：{host[:64]}')
        hosts.append(host)
    schemes = []
    for scheme in split_list(env('PORTAL_ALLOWED_SCHEMES')) or ['https', 'http']:
        scheme = scheme.lower()
        if scheme not in ('http', 'https'):
            raise ConfigError(f'PORTAL_ALLOWED_SCHEMES 只支持 http/https：{scheme[:20]}')
        if scheme not in schemes:
            schemes.append(scheme)
    mac_format = (env('PORTAL_MAC_FORMAT') or 'auto').lower()
    if mac_format not in ('auto', 'colon', 'hyphen'):
        raise ConfigError('PORTAL_MAC_FORMAT 必须是 auto、colon 或 hyphen')
    allow_any = as_bool(env('PORTAL_ALLOW_ANY') or '0', 'PORTAL_ALLOW_ANY')
    umac = env('PORTAL_UMAC')
    if umac and not is_placeholder(umac):
        umac = require_mac(umac, 'PORTAL_UMAC')
    else:
        umac = ''
    uplink = env('PORTAL_UPLINK_MAC')
    if uplink and not is_placeholder(uplink):
        uplink = require_mac(uplink, 'PORTAL_UPLINK_MAC')
    else:
        uplink = ''
    return {'allowedHosts': hosts, 'allowedSchemes': schemes,
            'allowAny': allow_any, 'macFormat': mac_format,
            'umac': umac, 'uplinkMac': uplink}


def main() -> int:
    try:
        creds = {'user': require_text(env('CREDS_USER'), 'CREDS_USER'),
                 'pass': require_text(env('CREDS_PASS'), 'CREDS_PASS')}
        mail = mail_config()
        macs = macs_config()
        location = location_config()
        portal = portal_config()
    except ConfigError as exc:
        print(f'[bootstrap] 配置错误：{exc}', file=sys.stderr, flush=True)
        return 2

    write('creds.json', creds)
    write('mail.json', mail)
    write('macs.json', {'macs': macs})
    write('location.json', location)
    write('portal.json', portal)
    print(f'[bootstrap] .env 配置已生成到 {OUT_DIR}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
