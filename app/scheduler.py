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

运行态：state/scheduler_state.json 持久化当日任务状态、预检状态和待重试时间，
容器重启后不会重复执行已完成任务，也不会丢失等待中的重试。
"""
import datetime
import json
import os
import random
import subprocess
import sys
import threading
import time

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from configutil import ConfigError, load_schedule
from logutil import append_log

DEFAULTS_DIR = os.environ.get('DEFAULTS_DIR', '/defaults')
STATE_DIR = os.environ.get('STATE_DIR', '/state')
SCHED = os.path.join(DEFAULTS_DIR, 'schedule.json')
STATE_FILE = os.path.join(STATE_DIR, 'scheduler_state.json')
LOG = os.path.join(STATE_DIR, 'logs', 'scheduler.log')
HEARTBEAT = os.path.join(STATE_DIR, 'heartbeat')

RETRY_CODES = (2, 3)      # 需要重试的退出码
GRACE_MIN = 90            # 错过触发点超过这个分钟数就不补跑
NOTIFY_WINDOW_MIN = 5     # 预检窗口收尾：距触发点不足 5 分钟就不再预检
MAX_ATTEMPTS = 2          # 首次 + 一次重试


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] [sched] {msg}"
    print(line, flush=True)
    append_log(LOG, line)


def spawn(name: str, argv: list) -> int:
    log(f"{name}: 执行 {' '.join(argv)}")
    try:
        result = subprocess.run(argv, timeout=1800)
        log(f"{name}: 退出码 {result.returncode}")
        return result.returncode
    except Exception as exc:
        log(f"{name}: 执行异常 {type(exc).__name__}: {exc}")
        return -1


class StateStore:
    """线程安全的当日调度状态，原子写入 state/scheduler_state.json。"""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.data = {'date': '', 'prechecks': {}, 'tasks': {}}

    def _default(self, today: str) -> dict:
        return {'date': today, 'prechecks': {}, 'tasks': {}}

    def _read(self) -> dict:
        try:
            with open(self.path, encoding='utf-8') as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                raise ValueError('不是 JSON 对象')
            if not isinstance(data.get('prechecks'), dict):
                data['prechecks'] = {}
            if not isinstance(data.get('tasks'), dict):
                data['tasks'] = {}
            return data
        except FileNotFoundError:
            return {}
        except Exception as exc:
            log(f'状态文件读取失败（{type(exc).__name__}: {exc}），按空状态启动')
            return {}

    def load(self, today: str) -> None:
        with self.lock:
            data = self._read()
            if data.get('date') != today:
                if data:
                    log(f'状态日期 {data.get("date")} 不是今天 {today}，重置当日状态')
                self.data = self._default(today)
            else:
                self.data = data
            self.save()

    def save(self) -> None:
        with self.lock:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                temp = self.path + '.tmp'
                with open(temp, 'w', encoding='utf-8') as handle:
                    json.dump(self.data, handle, ensure_ascii=False, indent=1)
                os.replace(temp, self.path)
                try:
                    os.chmod(self.path, 0o600)
                except Exception:
                    pass
            except Exception as exc:
                log(f'状态文件写入失败（{type(exc).__name__}: {exc}），仅保存在内存')

    def ensure_date(self, today: str) -> bool:
        with self.lock:
            if self.data.get('date') == today:
                return False
            self.data = self._default(today)
            self.save()
            return True

    def precheck_done(self, name: str) -> bool:
        with self.lock:
            return bool(self.data['prechecks'].get(name))

    def mark_precheck(self, name: str) -> None:
        with self.lock:
            self.data['prechecks'][name] = True
            self.save()

    def task(self, name: str) -> dict:
        with self.lock:
            value = self.data['tasks'].get(name)
            return dict(value) if isinstance(value, dict) else {}

    def set_task(self, name: str, **fields) -> None:
        with self.lock:
            current = self.data['tasks'].get(name)
            current = dict(current) if isinstance(current, dict) else {}
            current.update(fields)
            current['date'] = self.data['date']
            current['updated'] = time.time()
            self.data['tasks'][name] = current
            self.save()

    def start_task(self, name: str, attempt: int) -> str:
        with self.lock:
            self.set_task(name, status='running', attempts=attempt, next_retry_at=0)
            return self.data['date']

    def finish_task(self, name: str, rc: int, retry_min: int,
                    task_date: str = None) -> None:
        with self.lock:
            if task_date and task_date != self.data['date']:
                log(f'{name}: 任务跨日完成，忽略旧日期状态（{task_date}）')
                return
            current = self.data['tasks'].get(name)
            current = dict(current) if isinstance(current, dict) else {}
            if current.get('date') and current['date'] != self.data['date']:
                log(f'{name}: 任务跨日完成，忽略旧日期状态（{current["date"]}）')
                return
            attempts = int(current.get('attempts') or 1)
            if rc in (0, 6):
                status = 'done'
                next_retry_at = 0
            elif rc in RETRY_CODES and retry_min > 0 and attempts < MAX_ATTEMPTS:
                status = 'retry_pending'
                next_retry_at = time.time() + retry_min * 60
                log(f'{name}: rc={rc} 属失败码，{retry_min} 分钟后重试一次')
            else:
                status = 'failed'
                next_retry_at = 0
            current.update(status=status, rc=rc, next_retry_at=next_retry_at,
                           updated=time.time())
            self.data['tasks'][name] = current
            self.save()


def run_task(name: str, argv: list, delay_max: int, retry_min: int,
             store: StateStore = None, task_date: str = None) -> None:
    """线程内执行一次任务；重试由主循环按持久化状态安排。"""
    try:
        if delay_max > 0:
            delay = random.randint(0, delay_max)
            log(f'{name}: 随机延迟 {delay}s')
            time.sleep(delay)
        rc = spawn(name, argv)
    except Exception as exc:
        log(f'{name}: 线程异常 {type(exc).__name__}: {exc}')
        rc = -1
    if store is not None:
        store.finish_task(name, rc, retry_min, task_date)


def send_precheck(name: str, script: str) -> None:
    """线程内执行打卡前预检：脚本查询今日状态后，仅在“待打卡/预检失败”时发邮件"""
    spawn(f'{name}-precheck', ['python3', os.path.join(SCRIPTS, script), '--precheck'])


def write_heartbeat() -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(HEARTBEAT, 'w', encoding='utf-8') as handle:
            handle.write(str(int(time.time())))
        try:
            os.chmod(HEARTBEAT, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def build_tasks(sched: dict) -> list:
    tasks = []
    for name, label, precheck in (('checkin', '宿舍考勤打卡', 'checkin.py'),
                                  ('happy', '晚自习打卡', 'happy_checkin.py')):
        item = sched[name]
        tasks.append(dict(name=name, label=label,
                          hour=item['hour'], minute=item['minute'],
                          delay=item['random_delay_sec_max'],
                          retry=item['retry_after_min'],
                          notify=item['notify_before_min'],
                          precheck=precheck,
                          argv=['python3', os.path.join(SCRIPTS, precheck)]))
    return tasks


def target_of(now: datetime.datetime, task: dict) -> datetime.datetime:
    return now.replace(hour=task['hour'], minute=task['minute'],
                       second=0, microsecond=0)


def recover_running(store: StateStore, tasks: list, now: datetime.datetime) -> None:
    """进程重启后，把上次未完成（running）的任务转为可重试或失败。"""
    for task in tasks:
        current = store.task(task['name'])
        if current.get('status') != 'running':
            continue
        target = target_of(now, task)
        attempts = int(current.get('attempts') or 1)
        if now <= target + datetime.timedelta(minutes=GRACE_MIN) and attempts < MAX_ATTEMPTS:
            store.set_task(task['name'], status='retry_pending', next_retry_at=time.time())
            log(f"{task['name']}: 上次执行未完成，已恢复为待重试")
        else:
            store.set_task(task['name'], status='failed')
            log(f"{task['name']}: 上次执行未完成且已超出补跑窗口，标记失败")


def main() -> int:
    try:
        sched = load_schedule(SCHED, required=True)
    except ConfigError as exc:
        log(f'FAIL 调度配置错误：{exc}')
        return 1

    for warning in sched.get('_warnings', []):
        log(f'WARN 配置: {warning}')

    daily = build_tasks(sched)
    portal_interval = sched['portal']['interval_minutes']

    for task in daily:
        note = f"，提前 {task['notify']} 分钟预检（按状态选择性提醒）" if task['notify'] > 0 else ""
        log(f"任务 {task['name']}: 每日 {task['hour']:02d}:{task['minute']:02d}"
            f"（随机延迟 0~{task['delay']}s，失败 {task['retry']} 分钟后重试{note}）")
    log(f"任务 portal: 每 {portal_interval} 分钟一次")
    uid = os.getuid() if hasattr(os, 'getuid') else 'n/a'
    log(f"调度器启动完成（uid={uid}）")

    store = StateStore(STATE_FILE)
    now = datetime.datetime.now()
    store.load(now.date().isoformat())
    recover_running(store, daily, now)

    last_portal = 0.0
    max_loops = int(os.environ.get('RVTU_SCHEDULER_MAX_LOOPS', '0') or 0)
    loops = 0

    while True:
        now = datetime.datetime.now()
        store.ensure_date(now.date().isoformat())
        write_heartbeat()

        for task in daily:
            target = target_of(now, task)

            # ---- 打卡前预检 ----
            notify = task['notify']
            if notify > 0 and not store.precheck_done(task['name']):
                ntarget = target - datetime.timedelta(minutes=notify)
                deadline = target - datetime.timedelta(minutes=NOTIFY_WINDOW_MIN)
                if now >= ntarget:
                    store.mark_precheck(task['name'])
                    if now <= deadline:
                        threading.Thread(target=send_precheck,
                                         args=(task['name'], task['precheck']),
                                         daemon=True).start()
                    else:
                        log(f"{task['name']}: 预检窗口已错过（距触发不足 "
                            f"{NOTIFY_WINDOW_MIN} 分钟），跳过")

            # ---- 打卡任务 ----
            current = store.task(task['name'])
            status = current.get('status') or ''
            if status in ('done', 'failed', 'skipped', 'running'):
                continue
            if status == 'retry_pending':
                due = float(current.get('next_retry_at') or 0)
                if time.time() < due:
                    continue
                attempts = int(current.get('attempts') or 1)
                if now > target + datetime.timedelta(minutes=GRACE_MIN) or attempts >= MAX_ATTEMPTS:
                    store.set_task(task['name'], status='failed')
                    log(f"{task['name']}: 重试已超出补跑窗口或次数，标记失败")
                    continue
                task_date = store.start_task(task['name'], attempts + 1)
                log(f"{task['name']}: 开始第 {attempts + 1} 次尝试")
                threading.Thread(target=run_task,
                                 args=(task['name'], task['argv'], 0, task['retry'],
                                       store, task_date),
                                 daemon=True).start()
                continue

            if now < target:
                continue
            if now > target + datetime.timedelta(minutes=GRACE_MIN):
                store.set_task(task['name'], status='skipped')
                log(f"{task['name']}: 今日触发点 {task['hour']:02d}:{task['minute']:02d} "
                    f"已错过超过 {GRACE_MIN} 分钟，跳过")
                continue
            task_date = store.start_task(task['name'], 1)
            threading.Thread(target=run_task,
                             args=(task['name'], task['argv'], task['delay'],
                                   task['retry'], store, task_date),
                             daemon=True).start()

        if time.time() - last_portal >= portal_interval * 60:
            last_portal = time.time()
            threading.Thread(target=run_task,
                             args=('portal', ['sh', os.path.join(SCRIPTS, 'portal-auth.sh')],
                                   0, 0, None),
                             daemon=True).start()

        loops += 1
        if max_loops and loops >= max_loops:
            break
        time.sleep(20)

    return 0


if __name__ == '__main__':
    sys.exit(main())
