#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校园网 Portal 自动认证（Docker / Windows 通用版）

安全边界：
    - 只向 portal.allowedHosts 白名单内的 host 提交账号密码，默认 fail-closed；
    - 只允许 http/https，拒绝带 userinfo 的跳转；
    - 认证成功后重新探测 204，避免把认证接口响应误判为网络已恢复；
    - 账号密码不进入命令行参数，避免进程列表泄露。

用法：
    python3 portal_auth.py [iface ...]
    sh portal-auth.sh [iface ...]        # Docker 兼容入口（源码在 docker/portal-auth.sh）

Windows 说明：
    自动枚举本机 IPv4 地址并逐个探测；建议在 portal.json 填 umac，
    否则回退到 getmac / 主网卡 MAC，可能与实际联网网卡不一致。

配置：
    config/creds.json（Docker 内 /config/creds.json）    user / pass
    config/portal.json（Docker 内 /config/portal.json）   umac / uplinkMac / allowedHosts / allowedSchemes /
                          allowAny / macFormat
    defaults/api.json（Docker 内 /defaults/api.json）    probe_url
"""
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import uuid

from configutil import (ConfigError, DEFAULT_CONFIG_DIR, DEFAULT_DEFAULTS_DIR,
                        DEFAULT_STATE_DIR, MAC_RE, load_api, load_creds,
                        load_portal, mac_style, normalize_mac)
from logutil import log as log_line

CONFIG_DIR = os.environ.get('CONFIG_DIR', DEFAULT_CONFIG_DIR)
DEFAULTS_DIR = os.environ.get('DEFAULTS_DIR', DEFAULT_DEFAULTS_DIR)
STATE_DIR = os.environ.get('STATE_DIR', DEFAULT_STATE_DIR)
LOG = os.path.join(STATE_DIR, 'logs', 'portal-auth.log')
UA = 'RVTU-bot/1.0'
IS_WINDOWS = os.name == 'nt'


class IPv4HTTPConnection(http.client.HTTPConnection):
    """强制 IPv4，并把连接绑定到指定源 IP。"""

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        if self.source_address:
            self.sock.bind(self.source_address)
        self.sock.connect((socket.gethostbyname(self.host), self.port))


class IPv4HTTPSConnection(http.client.HTTPSConnection):
    """强制 IPv4 的 HTTPS 连接，保留证书主机名校验。"""

    def connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        if self.source_address:
            sock.bind(self.source_address)
        sock.connect((socket.gethostbyname(self.host), self.port))
        context = getattr(self, '_context', None)
        if context is None:
            import ssl
            context = ssl.create_default_context()
        self.sock = context.wrap_socket(sock, server_hostname=self.host)


def log(message: str) -> None:
    log_line(LOG, message)


def run_ip(args):
    try:
        result = subprocess.run(['ip'] + args, capture_output=True, text=True, timeout=8)
        return result.stdout if result.returncode == 0 else ''
    except Exception:
        return ''


def _decode_output(raw: bytes) -> str:
    for encoding in ('oem', 'mbcs', 'utf-8'):
        try:
            return raw.decode(encoding)
        except Exception:
            continue
    return raw.decode('latin-1', 'ignore')


def _win_ipv4_list():
    """枚举 Windows 本机 IPv4（排除回环/APIPA），返回 IP 列表作为接口标识。"""
    addresses = []
    try:
        result = subprocess.run(['ipconfig'], capture_output=True, timeout=8)
        output = _decode_output(result.stdout or b'')
    except Exception:
        output = ''
    for match in re.finditer(r'IPv4[^:]*:\s*(\d+\.\d+\.\d+\.\d+)', output, re.I):
        address = match.group(1)
        if address.startswith(('127.', '169.254.')) or address in addresses:
            continue
        addresses.append(address)
    if not addresses:
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                address = info[4][0]
                if address.startswith(('127.', '169.254.')) or address in addresses:
                    continue
                addresses.append(address)
        except Exception:
            pass
    return addresses


def _win_mac():
    """返回本机 MAC（连字符格式）；多网卡时优先 getmac 第一条。"""
    try:
        result = subprocess.run(['getmac', '/fo', 'csv', '/nh'], capture_output=True, timeout=8)
        output = _decode_output(result.stdout or b'')
        match = re.search(r'([0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5})', output)
        if match:
            return match.group(1)
    except Exception:
        pass
    try:
        node = uuid.getnode()
        if (node >> 40) & 0x01:
            return ''
        return '-'.join(f'{(node >> shift) & 0xff:02x}' for shift in range(40, -1, -8))
    except Exception:
        return ''


def if_ip(iface: str) -> str:
    if IS_WINDOWS:
        return iface if re.match(r'^\d+\.\d+\.\d+\.\d+$', iface or '') else ''
    output = run_ip(['-4', 'addr', 'show', 'dev', iface])
    match = re.search(r'\binet\s+(\d+\.\d+\.\d+\.\d+)/', output)
    return match.group(1) if match else ''


def if_mac(iface: str) -> str:
    if IS_WINDOWS:
        return _win_mac()
    output = run_ip(['link', 'show', 'dev', iface])
    match = re.search(r'link/ether\s+([0-9a-fA-F:]{17})', output)
    return match.group(1).replace(':', '-') if match else ''


def default_interfaces():
    if IS_WINDOWS:
        return _win_ipv4_list()
    interfaces = set()
    for line in run_ip(['route', 'show', 'default']).splitlines():
        match = re.search(r'\bdev\s+(\S+)', line)
        if match:
            interfaces.add(match.group(1))
    for line in run_ip(['-4', '-o', 'addr', 'show']).splitlines():
        match = re.match(r'\d+:\s+(\S+)\s+inet\s+(\d+\.\d+\.\d+\.\d+)/', line)
        if not match:
            continue
        iface, address = match.group(1), match.group(2)
        if address.startswith('10.') and not re.match(r'^(br-|docker|veth|lo)', iface):
            interfaces.add(iface)
    return [iface for iface in sorted(interfaces)
            if not re.match(r'^(br-|docker|veth|lo)', iface)]


def valid_iface(iface: str) -> bool:
    return bool(re.match(r'^[A-Za-z0-9_.:@-]+$', iface or ''))


def make_connection(host: str, port: int, scheme: str, source_ip: str, timeout: float):
    cls = IPv4HTTPSConnection if scheme == 'https' else IPv4HTTPConnection
    return cls(host, port, timeout=timeout, source_address=(source_ip, 0))


def probe(iface: str, source_ip: str, probe_url: str):
    """返回 (status, location)；无响应返回 None。"""
    try:
        parsed = urllib.parse.urlsplit(probe_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            return None
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        path = parsed.path or '/'
        if parsed.query:
            path += '?' + parsed.query
        conn = make_connection(parsed.hostname, port, parsed.scheme, source_ip, 8)
        try:
            conn.request('GET', path, headers={'Host': parsed.netloc, 'User-Agent': UA})
            response = conn.getresponse()
            status = response.status
            location = response.getheader('Location') or ''
            response.read(4096)
            return status, location
        finally:
            conn.close()
    except Exception:
        return None


def query_value(query: dict, name: str) -> str:
    values = query.get(name) or ['']
    return values[0]


def mac_for_login(portal: dict, location_mac: str, fallback_mac: str):
    style = portal['macFormat']
    if style == 'auto':
        reference = location_mac or portal['umac'] or fallback_mac
        style = mac_style(reference)
    else:
        style = ':' if style == 'colon' else '-'
    value = portal['umac'] or location_mac or fallback_mac
    value = normalize_mac(value, style) if value else ''
    if value and not MAC_RE.match(value):
        log(f'WARN 认证 MAC 格式非法，已忽略：{value[:32]}')
        value = ''
    uplink = portal['uplinkMac']
    uplink = normalize_mac(uplink, style) if uplink else ''
    if uplink and not MAC_RE.match(uplink):
        log(f'WARN 上联 MAC 格式非法，已忽略：{uplink[:32]}')
        uplink = ''
    return value, uplink


def login(iface: str, source_ip: str, location: str, probe_url: str,
          creds: dict, portal: dict) -> int:
    try:
        target = urllib.parse.urljoin(probe_url, location)
        parsed = urllib.parse.urlsplit(target)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.username or parsed.password:
            log(f'{iface}: blocked redirect with userinfo: {host[:64]}')
            return 1
        if scheme not in portal['allowedSchemes']:
            log(f'{iface}: blocked redirect scheme={scheme} host={host[:64]}')
            return 1
        if not host:
            log(f'{iface}: no portal host in redirect, skip')
            return 1
        if not portal['allowAny']:
            if host not in portal['allowedHosts']:
                log(f'{iface}: blocked redirect host={host[:64]}（不在 portal.allowedHosts）')
                return 1
        else:
            log('WARN portal.allowAny 已启用，跳过 host 白名单（不安全）')

        netloc = parsed.netloc
        base = f'{scheme}://{netloc}'
        if scheme == 'http':
            log(f'WARN {iface}: 使用 HTTP 提交门户凭据，链路可能被窃听')

        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        uaddress = query_value(query, 'uaddress') or source_ip
        location_mac = query_value(query, 'umac')
        acip = query_value(query, 'ac-ip')
        ssid = query_value(query, 'ssid')
        push_page = query_value(query, 'pushPageId')
        fallback_mac = '' if (portal['umac'] or location_mac) else if_mac(iface)
        umac, uplink = mac_for_login(portal, location_mac, fallback_mac)
        if not umac:
            log(f'WARN {iface}: 无可用认证 MAC，仍按空值提交（可能失败）')

        form = {
            'userName': creds['user'],
            'userPass': creds['pass'],
            'authType': '1',
            'uaddress': uaddress,
            'umac': umac,
            'acip': acip,
            'ssid': ssid,
            'pushPageId': push_page,
            'agreed': '1',
            'esn': '',
            'apmac': uplink,
            'armac': '',
            'accessMac': '',
            'businessType': '',
            'registerCode': '',
            'questions': '',
            'dynamicValidCode': '',
            'validCode': '',
        }
        body = urllib.parse.urlencode(form)
        port = parsed.port or (443 if scheme == 'https' else 80)
        conn = make_connection(host, port, scheme, source_ip, 15)
        try:
            conn.request('POST', '/portalauth/login', body=body, headers={
                'Host': netloc,
                'User-Agent': UA,
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-Requested-With': 'XMLHttpRequest',
            })
            response = conn.getresponse()
            raw = response.read(65536).decode('utf-8', 'ignore')
            status = response.status
        finally:
            conn.close()
    except Exception as exc:
        log(f'{iface}: re-auth 请求异常 {type(exc).__name__}: {exc}')
        return 1

    success = False
    message = ''
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            success = payload.get('success') is True
            message = str(payload.get('message') or '')
    except Exception:
        success = re.search(r'"success"\s*:\s*true', raw) is not None
        message = raw[:160]
    if not success:
        log(f'{iface}: re-auth FAIL http={status} {(message or raw)[:200]}')
        return 1

    log(f'{iface}: re-auth OK  ip={uaddress} mac={umac} ac={acip} portal={base}')
    verified = None
    for _ in range(2):
        time.sleep(1)
        verified = probe(iface, source_ip, probe_url)
        if verified and verified[0] == 204:
            log(f'{iface}: re-auth verified (204)')
            return 0
    if verified is None:
        log(f'{iface}: WARN re-auth 后探测无响应，无法确认网络状态')
    elif 300 <= verified[0] < 400:
        log(f'{iface}: WARN re-auth 后仍被重定向（http {verified[0]}）')
    else:
        log(f'{iface}: WARN re-auth 后探测 http {verified[0]}')
    return 1


def acquire_lock():
    path = os.path.join(STATE_DIR, 'portal.lock')
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        handle = open(path, 'a+')
    except Exception as exc:
        log(f'portal: 无法创建锁文件（{type(exc).__name__}），继续执行')
        return 'noop'
    try:
        if IS_WINDOWS:
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except BlockingIOError:
        handle.close()
        log('portal: 另一个实例正在执行，跳过本轮')
        return None
    except OSError:
        if IS_WINDOWS:
            handle.close()
            log('portal: 另一个实例正在执行，跳过本轮')
            return None
        return handle
    except Exception:
        return handle


def main() -> int:
    lock = acquire_lock()
    if lock is None:
        return 0
    try:
        try:
            api = load_api(os.path.join(DEFAULTS_DIR, 'api.json'))
            creds = load_creds(os.path.join(CONFIG_DIR, 'creds.json'))
            portal = load_portal(os.path.join(CONFIG_DIR, 'portal.json'))
        except ConfigError as exc:
            log(f'FAIL 配置错误：{exc}')
            return 2

        if not portal['allowedHosts'] and not portal['allowAny']:
            log('portal: allowedHosts 为空，默认拒绝提交凭据；'
                '请配置 portal.allowedHosts 或 PORTAL_ALLOWED_HOSTS')

        interfaces = sys.argv[1:] or default_interfaces()
        if not interfaces:
            log('no default route, skip')
            return 0

        for iface in interfaces:
            if not valid_iface(iface):
                log(f'{iface}: 非法接口名，跳过')
                continue
            address = if_ip(iface)
            if not address:
                continue
            result = probe(iface, address, api['probe_url'])
            if result is None:
                continue
            status, location = result
            if status == 204:
                continue
            if 300 <= status < 400:
                log(f'{iface}: redirected, auth -> {location}')
                login(iface, address, location, api['probe_url'], creds, portal)
            else:
                log(f'{iface}: probe http {status}')
        return 0
    finally:
        if lock != 'noop':
            try:
                lock.close()
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(main())
