#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打卡前提醒邮件（RVTU-bot）

由 scheduler.py 在打卡触发点前 N 分钟调用，把提醒发到 mail.json 配置的收件人。
提醒的作用：如果需要阻止本次打卡（例如人不在学校），在收到提醒后手动关闭容器。

配置：
    /config/mail.json    发信配置（与打卡共用）
    $STATE_DIR/logs/notify.log

用法:
    python3 notify.py "标题" "正文"           # 发送提醒
    python3 notify.py --dry-run "标题" "正文"  # 只打印不发送（测试用）

退出码: 0 成功 / 1 发送失败（配置缺失或 SMTP 异常）
"""
import os, sys, smtplib, ssl, datetime
from email.message import EmailMessage

from configutil import ConfigError, load_mail
from logutil import append_log

CONFIG_DIR = os.environ.get('CONFIG_DIR', '/config')
STATE_DIR = os.environ.get('STATE_DIR', '/state')
MAILCFG = os.path.join(CONFIG_DIR, 'mail.json')
LOGDIR = os.path.join(STATE_DIR, 'logs')
LOG = os.path.join(LOGDIR, 'notify.log')


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    append_log(LOG, line)


def send_mail(subject: str, body: str) -> bool:
    try:
        m = load_mail(MAILCFG)
    except ConfigError as exc:
        log(f'MAIL 配置错误：{exc}')
        return False
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
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    subject = args[0] if args else '【RVTU-bot】打卡提醒'
    body = args[1] if len(args) > 1 else subject

    if dry:
        log(f"DRY-RUN 将发送提醒 | 主题: {subject} | 正文: {body}")
        return 0
    return 0 if send_mail(subject, body) else 1


if __name__ == '__main__':
    sys.exit(main())
