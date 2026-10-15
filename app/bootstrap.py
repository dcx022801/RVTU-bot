#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .env 注入的环境变量合成为 config/*.json（仅 .env 模式启用时由 entrypoint.sh 调用）

背景：部署时可以选择不挂载 config/ 目录，而是把全部配置写在一个 .env 文件里
（docker compose 自动注入容器环境变量）。本模块负责把环境变量翻译成脚本读取的
JSON 配置文件，输出到 $ENV_CONFIG_DIR（默认 $STATE_DIR/config）。

变量命名对照（详见仓库根目录 .env.example）：
    CREDS_USER / CREDS_PASS                 登录凭据（身份证号 / 统一密码）
    MAIL_HOST / MAIL_PORT / MAIL_USER /     邮件通知
    MAIL_PASSWORD / MAIL_SENDER / MAIL_TO
    MACS                                    晚自习 MAC 池（逗号/空白分隔）
    LOCATION_LAT / LOCATION_LON /           定位通道坐标
    LOCATION_INFO
    PORTAL_UMAC / PORTAL_UPLINK_MAC         校园网认证两个 MAC（可留空）
"""
import json
import os
import re

STATE_DIR = os.environ.get('STATE_DIR', '/state')
OUT_DIR = os.environ.get('ENV_CONFIG_DIR') or os.path.join(STATE_DIR, 'config')


def env(name: str, default: str = '') -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    """读整数环境变量；空值或非法值回退默认"""
    try:
        v = str(os.environ.get(name, '') or '').strip()
        return int(v) if v else default
    except Exception:
        return default


def split_list(v: str):
    """逗号/分号/空白分隔 → 列表（用于 MACS 与 MAIL_TO）"""
    return [x.strip() for x in re.split(r'[,\s;]+', v or '') if x.strip()]


def write(name: str, obj: dict) -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def main() -> None:
    write('creds.json', {
        'user': env('CREDS_USER'),
        'pass': env('CREDS_PASS'),
    })
    write('mail.json', {
        'host': env('MAIL_HOST'),
        'port': env_int('MAIL_PORT', 465),
        'user': env('MAIL_USER'),
        'password': env('MAIL_PASSWORD'),
        'sender': env('MAIL_SENDER'),
        'to': split_list(env('MAIL_TO')),
    })
    write('macs.json', {
        'macs': split_list(env('MACS')),
    })
    write('location.json', {
        'latitude': env('LOCATION_LAT'),
        'longitude': env('LOCATION_LON'),
        'locationInfo': env('LOCATION_INFO'),
    })
    write('portal.json', {
        'umac': env('PORTAL_UMAC'),
        'uplinkMac': env('PORTAL_UPLINK_MAC'),
    })
    print(f'[bootstrap] .env 配置已生成到 {OUT_DIR}')


if __name__ == '__main__':
    main()
