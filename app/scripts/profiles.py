#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多用户配置与管理员账号存储。

目录结构（全部位于持久化 state 卷内）：
    state/users/<slug>/profile.json    显示名称 / 启用状态
    state/users/<slug>/config/*.json   该用户的 5 个配置
    state/users/<slug>/state/          该用户的登录态与日志
    state/web-admin.json               Docker Web 管理员（仅存哈希）
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from configutil import (REPO_ROOT, ConfigError, load_creds, load_location,
                        load_macs, load_mail, load_portal)

STATE_DIR = Path(os.environ.get('RVTU_STATE_DIR')
                 or os.environ.get('STATE_DIR')
                 or (Path(REPO_ROOT) / 'state')).expanduser()
USERS_DIR = Path(os.environ.get('RVTU_USERS_DIR')
                 or (STATE_DIR / 'users')).expanduser()
TEMPLATE_DIR = Path(os.environ.get('RVTU_TEMPLATE_DIR')
                    or (Path(REPO_ROOT) / 'defaults' / 'user-template')).expanduser()
ADMIN_FILE = STATE_DIR / 'web-admin.json'
RELOAD_FLAG = STATE_DIR / 'reload.flag'
CONFIG_FILES = ('creds.json', 'mail.json', 'macs.json',
                'location.json', 'portal.json')
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
ADMIN_ITERATIONS = 260000
_LOWER = 'abcdefghijklmnopqrstuvwxyz'
_UPPER = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
_DIGITS = '0123456789'
_SYMBOLS = '!@#$%^&*()-_=+[]{}:,.?'


class ProfileError(Exception):
    """用户配置操作失败。"""


def _json(path: Path, default=None):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def _write_json(path: Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    try:
        os.chmod(path, mode)
    except Exception:
        pass


def ensure_dirs() -> None:
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / 'logs').mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(USERS_DIR, 0o700)
    except Exception:
        pass


def valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug or ''))


def profile_path(slug: str) -> Path:
    if not valid_slug(slug):
        raise ProfileError('用户标识只能包含小写字母、数字、下划线、连字符（1~32 位）')
    path = (USERS_DIR / slug).resolve()
    if path.parent != USERS_DIR.resolve():
        raise ProfileError('用户标识非法')
    return path


def config_dir(slug: str) -> Path:
    return profile_path(slug) / 'config'


def runtime_dir(slug: str) -> Path:
    return profile_path(slug) / 'state'


def _meta(slug: str) -> dict:
    path = profile_path(slug) / 'profile.json'
    data = _json(path, None) or {}
    return {
        'name': str(data.get('name') or slug),
        'enabled': bool(data.get('enabled', False)),
        'created': float(data.get('created') or 0),
        'updated': float(data.get('updated') or 0),
    }


def save_meta(slug: str, name: str = None, enabled: bool = None) -> dict:
    current = _meta(slug)
    if name is not None:
        name = str(name).strip()
        if not name:
            raise ProfileError('用户名称不能为空')
        current['name'] = name
    if enabled is not None:
        current['enabled'] = bool(enabled)
    current['updated'] = time.time()
    if not current['created']:
        current['created'] = current['updated']
    _write_json(profile_path(slug) / 'profile.json', current)
    return current


def list_profiles(validate: bool = False) -> list:
    ensure_dirs()
    result = []
    for item in sorted(USERS_DIR.iterdir(), key=lambda p: p.name):
        if not item.is_dir() or item.name.startswith('.'):
            continue
        slug = item.name
        if not valid_slug(slug):
            continue
        meta = _meta(slug)
        cfg = item / 'config'
        errors, warnings = validate_dir(cfg) if validate else ([], [])
        result.append({
            'slug': slug,
            'name': meta['name'],
            'enabled': meta['enabled'],
            'created': meta['created'],
            'updated': meta['updated'],
            'config_dir': str(cfg),
            'state_dir': str(item / 'state'),
            'errors': errors,
            'warnings': warnings,
        })
    return result


def get_profile(slug: str, validate: bool = False) -> dict:
    if not profile_path(slug).is_dir():
        raise ProfileError(f'用户不存在：{slug}')
    meta = _meta(slug)
    cfg = config_dir(slug)
    errors, warnings = validate_dir(cfg) if validate else ([], [])
    return {
        'slug': slug,
        'name': meta['name'],
        'enabled': meta['enabled'],
        'created': meta['created'],
        'updated': meta['updated'],
        'config_dir': str(cfg),
        'state_dir': str(runtime_dir(slug)),
        'errors': errors,
        'warnings': warnings,
    }


def suggest_slug(name: str) -> str:
    base = re.sub(r'[^a-z0-9]+', '-', str(name or '').lower()).strip('-')
    if not base:
        base = 'user'
    base = base[:24]
    ensure_dirs()
    candidate = base
    index = 2
    while (USERS_DIR / candidate).exists() or not valid_slug(candidate):
        candidate = f'{base}-{index}'
        index += 1
    return candidate


def create_profile(name: str, slug: str = None, enabled: bool = False,
                   source_dir: str = None) -> dict:
    name = str(name or '').strip()
    if not name:
        raise ProfileError('用户名称不能为空')
    slug = str(slug or suggest_slug(name)).strip().lower()
    path = profile_path(slug)
    if path.exists():
        raise ProfileError(f'用户标识已存在：{slug}')
    cfg = path / 'config'
    (path / 'state' / 'logs').mkdir(parents=True, exist_ok=True)
    cfg.mkdir(parents=True, exist_ok=True)
    source = Path(source_dir) if source_dir else TEMPLATE_DIR
    for filename in CONFIG_FILES:
        target = cfg / filename
        src = source / filename
        if src.is_file():
            shutil.copyfile(src, target)
        else:
            _write_json(target, {})
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass
    now = time.time()
    _write_json(path / 'profile.json',
                {'name': name, 'enabled': bool(enabled),
                 'created': now, 'updated': now})
    touch_reload()
    return get_profile(slug)


def delete_profile(slug: str) -> str:
    path = profile_path(slug)
    if not path.is_dir():
        raise ProfileError(f'用户不存在：{slug}')
    trash = USERS_DIR / '.trash'
    trash.mkdir(parents=True, exist_ok=True)
    target = trash / f'{slug}-{int(time.time())}'
    shutil.move(str(path), str(target))
    touch_reload()
    return str(target)


def read_config(slug: str) -> dict:
    cfg = config_dir(slug)
    data = {}
    for filename in CONFIG_FILES:
        key = filename[:-5]
        data[key] = _json(cfg / filename, {}) or {}
    return data


def _write_config_dir(cfg: Path, data: dict) -> None:
    cfg.mkdir(parents=True, exist_ok=True)
    for filename in CONFIG_FILES:
        key = filename[:-5]
        value = data.get(key)
        if not isinstance(value, dict) and not isinstance(value, list):
            value = {}
        _write_json(cfg / filename, value)


def write_config(slug: str, data: dict) -> None:
    _write_config_dir(config_dir(slug), data)
    save_meta(slug)


def _validate_files(cfg: Path):
    errors = []
    warnings = []
    try:
        load_creds(str(cfg / 'creds.json'))
    except ConfigError as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f'creds.json：{exc}')
    try:
        load_macs(str(cfg / 'macs.json'))
    except ConfigError as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f'macs.json：{exc}')
    try:
        load_location(str(cfg / 'location.json'))
    except ConfigError as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f'location.json：{exc}')
    try:
        portal = load_portal(str(cfg / 'portal.json'))
        if not portal['allowedHosts'] and not portal['allowAny']:
            warnings.append('portal.allowedHosts 为空，校园网认证会拒绝提交凭据')
    except ConfigError as exc:
        errors.append(str(exc))
    except Exception as exc:
        errors.append(f'portal.json：{exc}')
    try:
        mail = load_mail(str(cfg / 'mail.json'))
        if mail is None:
            warnings.append('未启用邮件通知（mail.json 为空或仍是占位符）')
    except ConfigError as exc:
        warnings.append(f'邮件配置无效：{exc}')
    except Exception as exc:
        warnings.append(f'mail.json：{exc}')
    return errors, warnings


def validate_dir(cfg: Path):
    if not Path(cfg).is_dir():
        return ['缺少 config 目录'], []
    return _validate_files(Path(cfg))


def validate_data(data: dict):
    with tempfile.TemporaryDirectory(prefix='rvtu-profile-') as temp:
        cfg = Path(temp)
        _write_config_dir(cfg, data)
        return _validate_files(cfg)


def touch_reload() -> None:
    try:
        ensure_dirs()
        RELOAD_FLAG.write_text(str(time.time()), encoding='utf-8')
        try:
            os.chmod(RELOAD_FLAG, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def read_multi_state() -> dict:
    data = _json(STATE_DIR / 'multi_state.json', {})
    return data or {}


def legacy_config_dirs() -> list:
    raw = os.environ.get('RVTU_LEGACY_CONFIG_DIRS', '')
    candidates = [x.strip() for x in re.split(r'[;,]', raw) if x.strip()]
    candidates.extend([
        str(STATE_DIR / 'config'),
        '/config',
        str(Path(REPO_ROOT) / 'config'),
    ])
    seen = set()
    result = []
    for item in candidates:
        path = Path(item)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            result.append(path)
    return result


def ensure_default_profile():
    """首次启动迁移旧配置；没有可用旧配置时创建一个未启用的 default。"""
    ensure_dirs()
    existing = [p for p in USERS_DIR.iterdir()
                if p.is_dir() and not p.name.startswith('.')]
    if existing:
        return None
    for legacy in legacy_config_dirs():
        if not all((legacy / name).is_file() for name in CONFIG_FILES):
            continue
        errors, _warnings = validate_dir(legacy)
        if errors:
            continue
        profile = create_profile('默认用户', 'default', enabled=True,
                                 source_dir=str(legacy))
        return profile, str(legacy)
    profile = create_profile('默认用户', 'default', enabled=False)
    return profile, ''


def _random_password() -> str:
    chars = [secrets.choice(_LOWER), secrets.choice(_UPPER),
             secrets.choice(_DIGITS), secrets.choice(_SYMBOLS)]
    pool = _LOWER + _UPPER + _DIGITS + _SYMBOLS
    chars.extend(secrets.choice(pool) for _ in range(16))
    secrets.SystemRandom().shuffle(chars)
    return ''.join(chars)


def _random_username() -> str:
    chars = [secrets.choice(_LOWER), secrets.choice(_DIGITS)]
    pool = _LOWER + _DIGITS
    chars.extend(secrets.choice(pool) for _ in range(4))
    secrets.SystemRandom().shuffle(chars)
    return ''.join(chars)


def ensure_admin():
    """创建 Web 管理员；仅在首次创建时返回明文，用于容器日志展示一次。"""
    ensure_dirs()
    if ADMIN_FILE.is_file():
        return None
    username = _random_username()
    password = _random_password()
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                 salt, ADMIN_ITERATIONS)
    _write_json(ADMIN_FILE, {
        'username': username,
        'salt': base64.b64encode(salt).decode('ascii'),
        'hash': base64.b64encode(digest).decode('ascii'),
        'iterations': ADMIN_ITERATIONS,
        'created': time.time(),
    })
    return username, password


def verify_admin(username: str, password: str) -> bool:
    data = _json(ADMIN_FILE, None)
    if not data:
        return False
    if not hmac.compare_digest(str(data.get('username') or ''), str(username or '')):
        return False
    try:
        salt = base64.b64decode(data['salt'])
        expected = base64.b64decode(data['hash'])
        iterations = int(data.get('iterations') or ADMIN_ITERATIONS)
        digest = hashlib.pbkdf2_hmac('sha256', str(password or '').encode('utf-8'),
                                     salt, iterations)
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False
