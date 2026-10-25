#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RVTU-bot 配置读取与校验工具。

所有脚本共用：缺失、占位符、类型错误、范围错误都在发请求前拒绝，
避免把模板值或错误配置当作真实参数发到学校接口。
"""
import json
import re
import urllib.parse

PLACEHOLDER_WORDS = {'todo', 'changeme', 'example', 'placeholder', 'xxx'}
MAC_RE = re.compile(r'^(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$')
DEFAULT_BASE = 'https://rzptxsgl.rzpt.cn/xsxxgl-server'
DEFAULT_PROBE = 'http://connect.rom.miui.com/generate_204'
DEFAULT_UA_CHECKIN = (
    'Mozilla/5.0 (Linux; Android 16; 25079RPDCC Build/BP2A.250605.031.A3; wv) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/143.0.7499.192 Safari/537.36'
)
DEFAULT_UA_HAPPY = (
    'Mozilla/5.0 (Linux; Android 16; PLK110 Build/BP2A.250605.015; wv) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/154.0.8037.106 Mobile Safari/537.36'
)


class ConfigError(Exception):
    """配置缺失或非法。"""


def text(value):
    return str(value if value is not None else '').strip()


def load_json(path, required=True):
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except FileNotFoundError:
        if required:
            raise ConfigError(f'缺少配置文件 {path}')
        return None
    except Exception as exc:
        raise ConfigError(f'配置文件 {path} 解析失败：{type(exc).__name__}: {exc}')
    if not isinstance(data, dict):
        raise ConfigError(f'配置文件 {path} 必须是 JSON 对象')
    return data


def is_placeholder(value):
    value = text(value)
    if not value:
        return True
    if value.startswith('<') and value.endswith('>'):
        return True
    if value.lower() in PLACEHOLDER_WORDS:
        return True
    for word in ('身份证号', '统一密码', '邮箱授权码', '打卡点', '被认证网卡', '上联设备'):
        if word in value:
            return True
    return False


def require_text(value, name):
    value = text(value)
    if is_placeholder(value):
        raise ConfigError(f'{name} 未填写或仍为占位符')
    return value


def require_http_url(value, name):
    value = require_text(value, name)
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ConfigError(f'{name} 必须是合法的 http(s) 地址')
    return value


def require_int(value, name, minimum=None, maximum=None, default=None):
    if value is None or text(value) == '':
        if default is not None:
            return default
        raise ConfigError(f'{name} 不能为空')
    try:
        number = int(str(value).strip())
    except Exception:
        raise ConfigError(f'{name} 必须是整数')
    if minimum is not None and number < minimum:
        raise ConfigError(f'{name} 不能小于 {minimum}')
    if maximum is not None and number > maximum:
        raise ConfigError(f'{name} 不能大于 {maximum}')
    return number


def require_float(value, name, minimum=None, maximum=None):
    value = require_text(value, name)
    try:
        number = float(value)
    except Exception:
        raise ConfigError(f'{name} 必须是数字')
    if minimum is not None and number < minimum:
        raise ConfigError(f'{name} 不能小于 {minimum}')
    if maximum is not None and number > maximum:
        raise ConfigError(f'{name} 不能大于 {maximum}')
    return number, value


def as_bool(value, name='value'):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    normalized = text(value).lower()
    if normalized in ('true', '1', 'yes', 'y', 'on'):
        return True
    if normalized in ('false', '0', 'no', 'n', 'off'):
        return False
    raise ConfigError(f'{name} 不是合法的布尔值：{normalized[:40]}')


def optional_bool(value):
    try:
        return as_bool(value)
    except ConfigError:
        return None


def require_mac(value, name):
    value = text(value)
    if not MAC_RE.match(value):
        raise ConfigError(f'{name} 不是合法 MAC 地址：{value[:32]}')
    return value


def mac_style(*values):
    """根据已有值选择分隔符，默认连字符。"""
    for value in values:
        value = text(value)
        if ':' in value and MAC_RE.match(value):
            return ':'
        if '-' in value and MAC_RE.match(value):
            return '-'
    return '-'


def normalize_mac(value, style='-'):
    value = text(value).replace(':', '-').replace('-', style)
    return value


def decode_component(value):
    return urllib.parse.unquote(text(value))


def load_creds(path):
    data = load_json(path)
    return {
        'user': require_text(data.get('user'), 'creds.user'),
        'pass': require_text(data.get('pass'), 'creds.pass'),
    }


def load_mail(path):
    """返回 None 表示未启用邮件；文件存在但不完整则抛错。"""
    data = load_json(path, required=False)
    if data is None:
        return None
    recipients = data.get('to') or []
    if isinstance(recipients, str):
        recipients = [x.strip() for x in re.split(r'[,;\s]+', recipients) if x.strip()]
    if not isinstance(recipients, list):
        raise ConfigError('mail.to 必须是数组')
    recipients = [text(x) for x in recipients if text(x) and not is_placeholder(x)]
    fields = {key: text(data.get(key)) for key in ('host', 'user', 'password', 'sender')}
    user = '' if is_placeholder(fields['user']) else fields['user']
    password = '' if is_placeholder(fields['password']) else fields['password']
    sender = '' if is_placeholder(fields['sender']) else fields['sender']
    if not any((user, password, sender, recipients)):
        return None
    if not fields['host'] or is_placeholder(fields['host']) or not all((user, password, sender, recipients)):
        raise ConfigError('mail.json 配置不完整：host/user/password/sender/to 必须同时填写')
    port = require_int(data.get('port', 465), 'mail.port', 1, 65535)
    return {
        'host': fields['host'],
        'port': port,
        'user': user,
        'password': password,
        'sender': sender,
        'to': recipients,
    }


def load_macs(path):
    data = load_json(path)
    raw = data.get('macs')
    if not isinstance(raw, list) or not raw:
        raise ConfigError('macs.macs 必须是非空数组')
    return [require_mac(item, 'macs.macs[]') for item in raw]


def load_location(path):
    data = load_json(path)
    _lat, lat = require_float(data.get('latitude'), 'location.latitude', -90, 90)
    _lon, lon = require_float(data.get('longitude'), 'location.longitude', -180, 180)
    info = require_text(data.get('locationInfo'), 'location.locationInfo')
    return {'latitude': lat, 'longitude': lon, 'locationInfo': info}


def load_api(path, required=False):
    data = load_json(path, required=required) or {}
    base = require_http_url(data.get('base') or DEFAULT_BASE, 'api.base')
    if 'probe_url' in data and text(data.get('probe_url')):
        probe = require_http_url(data.get('probe_url'), 'api.probe_url')
    else:
        probe = DEFAULT_PROBE
    return {
        'base': base,
        'module': require_text(data.get('module') or 'sskq', 'api.module'),
        'ua_checkin': text(data.get('ua_checkin')) or DEFAULT_UA_CHECKIN,
        'ua_happy': text(data.get('ua_happy')) or DEFAULT_UA_HAPPY,
        'probe_url': probe,
    }


def load_schedule(path, required=False):
    data = load_json(path, required=required) or {}
    result = {'_warnings': []}
    for name, default_hour, default_minute in (('checkin', 21, 0), ('happy', 19, 0)):
        section = data.get(name) or {}
        hour = require_int(section.get('hour', default_hour), f'schedule.{name}.hour', 0, 23)
        minute = require_int(section.get('minute', default_minute), f'schedule.{name}.minute', 0, 59)
        delay = require_int(section.get('random_delay_sec_max', 59),
                            f'schedule.{name}.random_delay_sec_max')
        retry = require_int(section.get('retry_after_min', 20),
                            f'schedule.{name}.retry_after_min')
        notify = require_int(section.get('notify_before_min', 15),
                             f'schedule.{name}.notify_before_min')
        if delay < 0:
            result['_warnings'].append(f'schedule.{name}.random_delay_sec_max 为负数，已按 0 处理')
            delay = 0
        if retry < 0:
            result['_warnings'].append(f'schedule.{name}.retry_after_min 为负数，已按 0 处理（不重试）')
            retry = 0
        if notify < 0:
            result['_warnings'].append(f'schedule.{name}.notify_before_min 为负数，已按 0 处理')
            notify = 0
        elif 0 < notify <= 5:
            result['_warnings'].append(
                f'schedule.{name}.notify_before_min={notify} 会落在空预检窗口，'
                f'已自动关闭预检；如需预检请填 0 或 >=6'
            )
            notify = 0
        item = {'hour': hour, 'minute': minute, 'random_delay_sec_max': delay,
                'retry_after_min': retry, 'notify_before_min': notify}
        if name == 'happy':
            budget = require_int(section.get('budget_sec', 55), 'schedule.happy.budget_sec')
            if budget < 10:
                result['_warnings'].append('schedule.happy.budget_sec 小于 10，已按 55 处理')
                budget = 55
            item['budget_sec'] = budget
        result[name] = item
    portal = data.get('portal') or {}
    interval = require_int(portal.get('interval_minutes', 1),
                           'schedule.portal.interval_minutes')
    if interval < 1:
        result['_warnings'].append('schedule.portal.interval_minutes 小于 1，已按 1 处理')
        interval = 1
    result['portal'] = {'interval_minutes': interval}
    return result


def load_portal(path):
    data = load_json(path, required=False) or {}
    raw_hosts = data.get('allowedHosts') or []
    if isinstance(raw_hosts, str):
        raw_hosts = [x.strip() for x in re.split(r'[,;\s]+', raw_hosts) if x.strip()]
    if not isinstance(raw_hosts, list):
        raise ConfigError('portal.allowedHosts 必须是数组')
    hosts = []
    for item in raw_hosts:
        host = text(item).lower().rstrip('.')
        if not host:
            continue
        if host == '*':
            raise ConfigError('portal.allowedHosts 不允许通配符 *')
        if '://' in host or '/' in host or '@' in host:
            raise ConfigError(f'portal.allowedHosts 只能是主机名或 IP：{host[:64]}')
        hosts.append(host)
    raw_schemes = data.get('allowedSchemes') or ['https', 'http']
    if isinstance(raw_schemes, str):
        raw_schemes = [x.strip() for x in re.split(r'[,;\s]+', raw_schemes) if x.strip()]
    if not isinstance(raw_schemes, list):
        raise ConfigError('portal.allowedSchemes 必须是数组')
    schemes = []
    for item in raw_schemes:
        scheme = text(item).lower()
        if scheme not in ('http', 'https'):
            raise ConfigError(f'portal.allowedSchemes 只支持 http/https：{scheme[:20]}')
        if scheme not in schemes:
            schemes.append(scheme)
    mac_format = text(data.get('macFormat') or 'auto').lower()
    if mac_format not in ('auto', 'colon', 'hyphen'):
        raise ConfigError('portal.macFormat 必须是 auto、colon 或 hyphen')
    umac = text(data.get('umac'))
    if umac and not is_placeholder(umac):
        umac = require_mac(umac, 'portal.umac')
    elif is_placeholder(umac):
        umac = ''
    uplink = text(data.get('uplinkMac'))
    if uplink and not is_placeholder(uplink):
        uplink = require_mac(uplink, 'portal.uplinkMac')
    elif is_placeholder(uplink):
        uplink = ''
    return {
        'allowedHosts': hosts,
        'allowedSchemes': schemes,
        'allowAny': as_bool(data.get('allowAny', False), 'portal.allowAny'),
        'macFormat': mac_format,
        'umac': umac,
        'uplinkMac': uplink,
    }
