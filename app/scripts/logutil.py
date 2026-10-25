#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RVTU-bot 日志工具：stdout + 文件双写，单文件按大小轮转。"""
import datetime
import os

MAX_LOG_BYTES = 5 * 1024 * 1024


def append_log(path, line, max_bytes=MAX_LOG_BYTES):
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) >= max_bytes:
            os.replace(path, path + '.1')
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(line.rstrip('\n') + '\n')
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def log(path, message, max_bytes=MAX_LOG_BYTES):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(line, flush=True)
    append_log(path, line, max_bytes)
    return line
