#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RVTU-bot 内置调度器（替代宿主 cron）

读取 /defaults/schedule.json，到点执行三个任务：
    checkin  每日 21:00（随机延迟后执行 checkin.py）—— 宿舍考勤打卡
    happy    每日 19:00（随机延迟后执行 happy_checkin.py）—— 晚自习打卡
    portal   每 interval_minutes 分钟执行 portal-auth.sh —— 校园网 Portal 认证

打卡前预检：每个打卡任务在触发点前 notify_before_min 分钟执行一次预检
（即调用对应脚本的 --precheck 模式：查询今日状态后，仅在"待打卡/预检失败"时发邮件；
窗口 = 触发点前 notify_before_min 分钟 ~ 前 5 分钟，错过窗口不执行；0 = 关闭）。
失败重试：任务退出码为 2/3 时，在 retry_after_min 分钟后重试一次；其余不重试。
错过触发点超过 GRACE_MIN 分钟（如深夜启动）则不补跑。
"""
import datetime
import json
import os
import random
import subprocess
import threading
import time

DEFAULTS_DIR = os.environ.get('DEFAULTS_DIR', '/defaults')
STATE_DIR = os.environ.get('STATE_DIR', '/state')
SCHED = os.path.join(DEFAULTS_DIR, 'schedule.json')
SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')

RETRY_CODES = (2, 3)      # 需要重试的退出码
GRACE_MIN = 90            # 错过触发点超过这个分钟数就不补跑
NOTIFY_WINDOW_MIN = 5     # 预检窗口收尾：距触发点不足 5 分钟就不再预检


def log(msg: str) -> None:
    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] [sched] {msg}", flush=True)


def load_json(path: str):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def spawn(name: str, argv: list) -> int:
    log(f"{name}: 执行 {' '.join(argv)}")
    try:
        r = subprocess.run(argv, timeout=1800)
        log(f"{name}: 退出码 {r.returncode}")
        return r.returncode
    except Exception as e:
        log(f"{name}: 执行异常 {type(e).__name__}: {e}")
        return -1


def run_task(name: str, argv: list, delay_max: int, retry_min: int) -> None:
    """线程内运行：随机延迟 → 执行 → 失败按配置重试一次。"""
    if delay_max > 0:
        d = random.randint(0, delay_max)
        log(f"{name}: 随机延迟 {d}s")
        time.sleep(d)
    rc = spawn(name, argv)
    if rc in RETRY_CODES and retry_min > 0:
        log(f"{name}: rc={rc} 属失败码，{retry_min} 分钟后重试一次")
        time.sleep(retry_min * 60)
        rc2 = spawn(name, argv)
        log(f"{name}: 重试完成 rc={rc2}")


def send_precheck(name: str, script: str) -> None:
    """线程内执行打卡前预检：脚本查询今日状态后，仅在“待打卡/预检失败”时发邮件"""
    spawn(f'{name}-precheck', ['python3', os.path.join(SCRIPTS, script), '--precheck'])


def main() -> None:
    sched = load_json(SCHED) or {}

    daily = []
    c = sched.get('checkin') or {}
    daily.append(dict(name='checkin', label='宿舍考勤打卡',
                      hour=int(c.get('hour', 21)), minute=int(c.get('minute', 0)),
                      delay=int(c.get('random_delay_sec_max', 59)),
                      retry=int(c.get('retry_after_min', 20)),
                      notify=int(c.get('notify_before_min', 15)),
                      precheck='checkin.py',
                      argv=['python3', os.path.join(SCRIPTS, 'checkin.py')]))
    h = sched.get('happy') or {}
    daily.append(dict(name='happy', label='晚自习打卡',
                      hour=int(h.get('hour', 19)), minute=int(h.get('minute', 0)),
                      delay=int(h.get('random_delay_sec_max', 59)),
                      retry=int(h.get('retry_after_min', 20)),
                      notify=int(h.get('notify_before_min', 15)),
                      precheck='happy_checkin.py',
                      argv=['python3', os.path.join(SCRIPTS, 'happy_checkin.py')]))
    p = sched.get('portal') or {}
    portal_interval = int(p.get('interval_minutes', 1))

    for t in daily:
        note = f"，提前 {t['notify']} 分钟预检（按状态选择性提醒）" if t['notify'] > 0 else ""
        log(f"任务 {t['name']}: 每日 {t['hour']:02d}:{t['minute']:02d}"
            f"（随机延迟 0~{t['delay']}s，失败 {t['retry']} 分钟后重试{note}）")
    log(f"任务 portal: 每 {portal_interval} 分钟一次")
    log("调度器启动完成")

    fired = set()
    last_portal = 0.0

    while True:
        now = datetime.datetime.now()
        for t in daily:
            target = now.replace(hour=t['hour'], minute=t['minute'], second=0, microsecond=0)

            # ---- 打卡前预检 ----
            nb = t['notify']
            if nb > 0:
                nkey = (t['name'] + '-precheck', now.date())
                if nkey not in fired:
                    ntarget = target - datetime.timedelta(minutes=nb)
                    deadline = target - datetime.timedelta(minutes=NOTIFY_WINDOW_MIN)
                    if now >= ntarget:
                        fired.add(nkey)
                        if now <= deadline:
                            threading.Thread(target=send_precheck,
                                             args=(t['name'], t['precheck']),
                                             daemon=True).start()
                        else:
                            log(f"{t['name']}: 预检窗口已错过（距触发不足 {NOTIFY_WINDOW_MIN} 分钟），跳过")

            # ---- 打卡任务 ----
            key = (t['name'], now.date())
            if key in fired:
                continue
            if now < target:
                continue
            if (now - target).total_seconds() > GRACE_MIN * 60:
                fired.add(key)
                log(f"{t['name']}: 今日触发点 {t['hour']:02d}:{t['minute']:02d} 已错过超过 {GRACE_MIN} 分钟，跳过")
                continue
            fired.add(key)
            threading.Thread(target=run_task,
                             args=(t['name'], t['argv'], t['delay'], t['retry']),
                             daemon=True).start()

        if time.time() - last_portal >= portal_interval * 60:
            last_portal = time.time()
            threading.Thread(target=run_task,
                             args=('portal', ['sh', os.path.join(SCRIPTS, 'portal-auth.sh')], 0, 0),
                             daemon=True).start()

        time.sleep(20)


if __name__ == '__main__':
    main()
