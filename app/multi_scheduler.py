#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RVTU-bot 多用户调度器

- 所有启用用户共用 defaults/schedule.json 的时间表；
- 打卡前预检时随机决定用户顺序（A/B 演示：随机决定谁先）；
- 到点后按该顺序拉起各用户的打卡任务，相邻用户间隔 1~10 秒（可配置）；
- 每个用户独立 CONFIG_DIR / STATE_DIR，登录态、日志、邮件互不影响；
- Portal 每分钟按随机顺序、同样间隔探测各用户。
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

from configutil import (ConfigError, DEFAULT_DEFAULTS_DIR, DEFAULT_STATE_DIR,
                        load_schedule)
from logutil import append_log
import profiles

FROZEN = bool(getattr(sys, 'frozen', False))
PYTHON = sys.executable or 'python3'
DEFAULTS_DIR = os.environ.get('DEFAULTS_DIR', DEFAULT_DEFAULTS_DIR)
STATE_DIR = os.environ.get('STATE_DIR', DEFAULT_STATE_DIR)
USERS_DIR = profiles.USERS_DIR
SCHED = os.path.join(DEFAULTS_DIR, 'schedule.json')
STATE_FILE = os.path.join(STATE_DIR, 'multi_state.json')
LOG = os.path.join(STATE_DIR, 'logs', 'multi.log')
HEARTBEAT = os.path.join(STATE_DIR, 'heartbeat')
PID_FILE = os.path.join(STATE_DIR, 'multi.pid')
LOCK_FILE = os.path.join(STATE_DIR, 'multi.lock')
RELOAD_FLAG = os.path.join(STATE_DIR, 'reload.flag')

RETRY_CODES = (2, 3)
GRACE_MIN = 90
NOTIFY_WINDOW_MIN = 5
MAX_ATTEMPTS = 2


def log(message: str) -> None:
    line = f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] [multi] {message}'
    print(line, flush=True)
    append_log(LOG, line)


def profile_log(slug: str, message: str) -> None:
    line = f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {message}'
    append_log(os.path.join(USERS_DIR, slug, 'state', 'logs', 'multi.log'), line)


def core_argv(script: str, *args) -> list:
    path = os.path.join(SCRIPTS, script)
    if FROZEN:
        return [sys.executable, '--run-core', path, *args]
    return [PYTHON, path, *args]


def profile_env(profile: dict) -> dict:
    env = os.environ.copy()
    env.update({
        'CONFIG_DIR': profile['config_dir'],
        'STATE_DIR': profile['state_dir'],
        'DEFAULTS_DIR': DEFAULTS_DIR,
        'RVTU_PROFILE': profile['slug'],
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUNBUFFERED': '1',
        'PYTHONUTF8': '1',
    })
    return env


def run_script(profile: dict, label: str, script: str, *args,
               timeout: int = 1800) -> int:
    argv = core_argv(script, *args)
    slug = profile['slug']
    command = ' '.join(argv)
    log(f'{slug}/{label}: 执行 {command}')
    profile_log(slug, f'{label}: 执行')
    try:
        kwargs = {'timeout': timeout, 'env': profile_env(profile)}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(argv, **kwargs)
        log(f'{slug}/{label}: 退出码 {result.returncode}')
        profile_log(slug, f'{label}: 退出码 {result.returncode}')
        return result.returncode
    except Exception as exc:
        log(f'{slug}/{label}: 执行异常 {type(exc).__name__}: {exc}')
        profile_log(slug, f'{label}: 执行异常 {type(exc).__name__}: {exc}')
        return -1


class StateStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._default('')

    @staticmethod
    def _default(today: str) -> dict:
        return {
            'date': today,
            'orders': {},
            'prechecks': {},
            'launched': {},
            'tasks': {},
            'heartbeats': {},
        }

    def _read(self) -> dict:
        try:
            with open(self.path, encoding='utf-8') as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                return {}
            for key, default in self._default('').items():
                if not isinstance(data.get(key), type(default)):
                    data[key] = default
            return data
        except FileNotFoundError:
            return {}
        except Exception as exc:
            log(f'状态文件读取失败（{type(exc).__name__}: {exc}），按空状态启动')
            return {}

    def load(self, today: str) -> None:
        with self.lock:
            data = self._read()
            self.data = data if data.get('date') == today else self._default(today)
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

    def ensure_date(self, today: str) -> None:
        with self.lock:
            if self.data.get('date') != today:
                self.data = self._default(today)
                self.save()

    def recovery(self) -> None:
        """上次进程中断时 queued/running 的任务转为立即重试（打卡脚本幂等）。"""
        with self.lock:
            now = time.time()
            for slug, tasks in self.data['tasks'].items():
                for name, item in tasks.items():
                    if not isinstance(item, dict):
                        continue
                    if item.get('status') in ('queued', 'running'):
                        attempts = int(item.get('attempts') or 1)
                        if attempts < MAX_ATTEMPTS:
                            item.update(status='retry_pending', next_retry_at=now,
                                        updated=time.time())
                        else:
                            item.update(status='failed', updated=time.time())
            self.save()

    def order(self, task: str) -> list:
        with self.lock:
            value = self.data['orders'].get(task)
            return list(value) if isinstance(value, list) else []

    def set_order(self, task: str, order: list) -> None:
        with self.lock:
            self.data['orders'][task] = list(order)
            self.save()

    def precheck_done(self, task: str) -> bool:
        with self.lock:
            return bool(self.data['prechecks'].get(task))

    def mark_precheck(self, task: str) -> None:
        with self.lock:
            self.data['prechecks'][task] = True
            self.save()

    def launched(self, task: str) -> bool:
        with self.lock:
            return bool(self.data['launched'].get(task))

    def mark_launched(self, task: str) -> None:
        with self.lock:
            self.data['launched'][task] = True
            self.save()

    def task(self, slug: str, task: str) -> dict:
        with self.lock:
            value = (self.data['tasks'].get(slug) or {}).get(task)
            return dict(value) if isinstance(value, dict) else {}

    def set_task(self, slug: str, task: str, **fields) -> None:
        with self.lock:
            current = (self.data['tasks'].get(slug) or {}).get(task)
            current = dict(current) if isinstance(current, dict) else {}
            current.update(fields)
            current['date'] = self.data['date']
            current['updated'] = time.time()
            self.data['tasks'].setdefault(slug, {})[task] = current
            self.save()

    def queue_task(self, slug: str, task: str) -> None:
        self.set_task(slug, task, status='queued', attempts=0,
                      next_retry_at=0)

    def start_task(self, slug: str, task: str, attempt: int) -> None:
        self.set_task(slug, task, status='running', attempts=attempt,
                      next_retry_at=0)

    def finish_task(self, slug: str, task: str, rc: int,
                    retry_min: int, task_date: str = None) -> None:
        with self.lock:
            if task_date and task_date != self.data['date']:
                return
            item = self.task(slug, task)
            if item.get('date') and item['date'] != self.data['date']:
                return
            attempts = int(item.get('attempts') or 1)
            if rc in (0, 6):
                status, next_retry = 'done', 0
            elif rc in RETRY_CODES and retry_min > 0 and attempts < MAX_ATTEMPTS:
                status = 'retry_pending'
                next_retry = time.time() + retry_min * 60
                log(f'{slug}/{task}: 失败码 rc={rc}，{retry_min} 分钟后重试')
            else:
                status, next_retry = 'failed', 0
            self.set_task(slug, task, status=status, rc=rc,
                          next_retry_at=next_retry)

    def heartbeat(self, slug: str) -> None:
        with self.lock:
            self.data['heartbeats'][slug] = time.time()
            self.save()

    def prune(self, slugs: set) -> None:
        with self.lock:
            for slug in list(self.data['tasks']):
                if slug not in slugs:
                    self.data['tasks'].pop(slug, None)
            for slug in list(self.data['heartbeats']):
                if slug not in slugs:
                    self.data['heartbeats'].pop(slug, None)
            for task, order in list(self.data['orders'].items()):
                filtered = [s for s in order if s in slugs]
                if filtered:
                    self.data['orders'][task] = filtered
                else:
                    self.data['orders'].pop(task, None)


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


def acquire_instance_lock():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        handle = open(LOCK_FILE, 'a+')
    except Exception:
        return 'noop'
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except Exception:
        try:
            handle.close()
        except Exception:
            pass
        return None


def write_pid() -> None:
    try:
        with open(PID_FILE, 'w', encoding='utf-8') as handle:
            handle.write(str(os.getpid()))
        try:
            os.chmod(PID_FILE, 0o600)
        except Exception:
            pass
    except Exception:
        pass


def remove_pid() -> None:
    try:
        with open(PID_FILE, encoding='utf-8') as handle:
            if handle.read().strip() != str(os.getpid()):
                return
        os.remove(PID_FILE)
    except Exception:
        pass


def build_tasks(sched: dict) -> list:
    tasks = []
    for name, label, script in (('checkin', '宿舍考勤打卡', 'checkin.py'),
                                ('happy', '晚自习打卡', 'happy_checkin.py')):
        item = sched[name]
        tasks.append({
            'name': name,
            'label': label,
            'script': script,
            'hour': item['hour'],
            'minute': item['minute'],
            'retry': item['retry_after_min'],
            'notify': item['notify_before_min'],
        })
    return tasks


def target_of(now: datetime.datetime, task: dict) -> datetime.datetime:
    return now.replace(hour=task['hour'], minute=task['minute'],
                       second=0, microsecond=0)


def enabled_profiles(profiles_list: list) -> list:
    return [p for p in profiles_list if p['enabled'] and not p['errors']]


def task_enabled(profile: dict, task: str) -> bool:
    return bool((profile.get('tasks') or {}).get(task, True))


def task_profiles(profiles_list: list, task: str) -> list:
    return [p for p in enabled_profiles(profiles_list)
            if task_enabled(p, task)]


def config_errors(profiles_list: list) -> dict:
    return {p['slug']: p['errors'] for p in profiles_list
            if p['enabled'] and p['errors']}


def random_order(slugs: list) -> list:
    return random.sample(slugs, len(slugs))


def run_precheck(task: dict, order: list, by_slug: dict, stop_event) -> None:
    for index, slug in enumerate(order):
        if stop_event and stop_event.is_set():
            return
        profile = by_slug.get(slug)
        if not profile or not profile['enabled'] or profile['errors']                 or not task_enabled(profile, task['name']):
            continue
        run_script(profile, f'{task["name"]}-precheck', task['script'],
                   '--precheck', timeout=900)
        if index < len(order) - 1:
            time.sleep(random.randint(1, 3))


def run_task(profile: dict, task: dict, store: StateStore,
             task_date: str, attempt: int) -> None:
    rc = run_script(profile, task['name'], task['script'])
    store.finish_task(profile['slug'], task['name'], rc,
                      task['retry'], task_date)


def launch_sequence(task: dict, order: list, by_slug: dict,
                    store: StateStore, task_date: str, gaps: tuple,
                    stop_event) -> None:
    gap_min, gap_max = gaps
    for index, slug in enumerate(order):
        if stop_event and stop_event.is_set():
            return
        profile = by_slug.get(slug)
        if not profile or not profile['enabled'] or profile['errors']                 or not task_enabled(profile, task['name']):
            store.set_task(slug, task['name'], status='skipped',
                           rc=None, next_retry_at=0)
            log(f'{slug}/{task["name"]}: 用户已禁用、任务已关闭或配置无效，跳过')
            continue
        store.start_task(slug, task['name'], 1)
        threading.Thread(target=run_task,
                         args=(profile, task, store, task_date, 1),
                         daemon=True).start()
        if index < len(order) - 1:
            time.sleep(random.randint(gap_min, gap_max))


def run_portal(profile: dict) -> None:
    run_script(profile, 'portal', 'portal_auth.py', timeout=600)


def portal_sequence(order: list, by_slug: dict, gaps: tuple,
                    stop_event) -> None:
    gap_min, gap_max = gaps
    for index, slug in enumerate(order):
        if stop_event and stop_event.is_set():
            return
        profile = by_slug.get(slug)
        if profile and profile['enabled'] and not profile['errors']                 and task_enabled(profile, 'portal'):
            threading.Thread(target=run_portal, args=(profile,),
                             daemon=True).start()
        if index < len(order) - 1:
            time.sleep(random.randint(gap_min, gap_max))


def main(stop_event=None) -> int:
    try:
        sched = load_schedule(SCHED, required=True)
    except ConfigError as exc:
        log(f'FAIL 调度配置错误：{exc}')
        return 1
    for warning in sched.get('_warnings', []):
        log(f'WARN 配置: {warning}')

    daily = build_tasks(sched)
    portal_interval = sched['portal']['interval_minutes']
    gaps = (sched['multi']['launch_gap_min_sec'],
            sched['multi']['launch_gap_max_sec'])
    log(f'多用户调度器启动：任务 {daily[0]["hour"]:02d}:{daily[0]["minute"]:02d} / '
        f'{daily[1]["hour"]:02d}:{daily[1]["minute"]:02d}，Portal 每 '
        f'{portal_interval} 分钟，用户间隔 {gaps[0]}~{gaps[1]}s')

    lock = acquire_instance_lock()
    if lock is None:
        log('另一个多用户调度器实例正在运行，退出')
        return 1
    write_pid()

    store = StateStore(STATE_FILE)
    now = datetime.datetime.now()
    store.load(now.date().isoformat())
    store.recovery()

    last_portal = 0.0
    max_loops = int(os.environ.get('RVTU_SCHEDULER_MAX_LOOPS', '0') or 0)
    loops = 0
    reported = {}
    waiting = set()
    last_reload_mtime = 0.0

    try:
        while True:
            if stop_event and stop_event.is_set():
                break
            now = datetime.datetime.now()
            today = now.date().isoformat()
            store.ensure_date(today)

            try:
                mtime = os.path.getmtime(RELOAD_FLAG)
                if mtime != last_reload_mtime:
                    last_reload_mtime = mtime
                    log('检测到用户配置变更，重新加载')
                    sched = load_schedule(SCHED, required=True)
                    daily = build_tasks(sched)
                    portal_interval = sched['portal']['interval_minutes']
                    gaps = (sched['multi']['launch_gap_min_sec'],
                            sched['multi']['launch_gap_max_sec'])
            except OSError:
                pass
            except ConfigError as exc:
                log(f'WARN 重新加载调度配置失败，继续使用旧配置：{exc}')

            profiles_list = profiles.list_profiles(validate=True)
            valid = enabled_profiles(profiles_list)
            by_slug = {p['slug']: p for p in profiles_list}
            slugs = [p['slug'] for p in valid]
            store.prune({p['slug'] for p in profiles_list})

            for slug, errors in config_errors(profiles_list).items():
                key = (slug, tuple(errors))
                if reported.get(slug) != key:
                    reported[slug] = key
                    log(f'WARN 用户 {slug} 配置无效，暂不调度：{"；".join(errors)}')
            for slug in list(reported):
                if slug not in by_slug or not by_slug[slug]['errors']:
                    reported.pop(slug, None)

            for task in daily:
                target = target_of(now, task)
                task_slugs = [p['slug'] for p in
                              task_profiles(profiles_list, task['name'])]

                if task['notify'] > 0 and not store.precheck_done(task['name']):
                    ntarget = target - datetime.timedelta(minutes=task['notify'])
                    deadline = target - datetime.timedelta(minutes=NOTIFY_WINDOW_MIN)
                    if now >= ntarget:
                        store.mark_precheck(task['name'])
                        if now <= deadline:
                            order = random_order(task_slugs)
                            store.set_order(task['name'], order)
                            log(f'{task["name"]}: 预检决定用户顺序 '
                                f'{" -> ".join(order) if order else "（无用户）"}')
                            threading.Thread(
                                target=run_precheck,
                                args=(task, order, by_slug, stop_event),
                                daemon=True).start()
                        else:
                            log(f'{task["name"]}: 预检窗口已错过，跳过')

                if not store.launched(task['name']):
                    if now < target:
                        continue
                    if now > target + datetime.timedelta(minutes=GRACE_MIN):
                        for slug in task_slugs:
                            store.set_task(slug, task['name'], status='skipped',
                                           rc=None, next_retry_at=0)
                        store.mark_launched(task['name'])
                        log(f'{task["name"]}: 已错过超过 {GRACE_MIN} 分钟，今日跳过')
                        continue
                    order = store.order(task['name'])
                    order = [s for s in order if s in task_slugs]
                    for slug in task_slugs:
                        if slug not in order:
                            order.append(slug)
                    if not order:
                        order = random_order(task_slugs)
                    if not order:
                        if task['name'] not in waiting:
                            waiting.add(task['name'])
                            log(f'{task["name"]}: 当前没有启用该任务且配置有效的用户，等待配置')
                        continue
                    waiting.discard(task['name'])
                    store.set_order(task['name'], order)
                    for slug in order:
                        store.queue_task(slug, task['name'])
                    store.mark_launched(task['name'])
                    log(f'{task["name"]}: 按顺序拉起 {" -> ".join(order) if order else "（无用户）"}')
                    task_date = store.data['date']
                    threading.Thread(
                        target=launch_sequence,
                        args=(task, order, by_slug, store, task_date, gaps, stop_event),
                        daemon=True).start()

                for slug in task_slugs:
                    current = store.task(slug, task['name'])
                    if current.get('status') != 'retry_pending':
                        continue
                    due = float(current.get('next_retry_at') or 0)
                    if time.time() < due:
                        continue
                    attempts = int(current.get('attempts') or 0)
                    if now > target + datetime.timedelta(minutes=GRACE_MIN) \
                            or attempts >= MAX_ATTEMPTS:
                        store.set_task(slug, task['name'], status='failed',
                                       next_retry_at=0)
                        continue
                    profile = by_slug.get(slug)
                    if not profile or not profile['enabled'] or profile['errors']                             or not task_enabled(profile, task['name']):
                        store.set_task(slug, task['name'], status='failed')
                        continue
                    store.start_task(slug, task['name'], attempts + 1)
                    task_date = store.data['date']
                    log(f'{slug}/{task["name"]}: 开始第 {attempts + 1} 次尝试')
                    threading.Thread(
                        target=run_task,
                        args=(profile, task, store, task_date, attempts + 1),
                        daemon=True).start()

            if time.time() - last_portal >= portal_interval * 60:
                last_portal = time.time()
                portal_slugs = [p['slug'] for p in
                                task_profiles(profiles_list, 'portal')]
                order = random_order(portal_slugs)
                if order:
                    log(f'portal: 按顺序探测 {" -> ".join(order)}')
                    threading.Thread(
                        target=portal_sequence,
                        args=(order, by_slug, gaps, stop_event),
                        daemon=True).start()

            write_heartbeat()
            for slug in slugs:
                store.heartbeat(slug)
            store.save()

            loops += 1
            if max_loops and loops >= max_loops:
                break
            if stop_event:
                stop_event.wait(5)
            else:
                time.sleep(5)
        return 0
    finally:
        remove_pid()
        if lock != 'noop':
            try:
                lock.close()
            except Exception:
                pass


if __name__ == '__main__':
    sys.exit(main())
