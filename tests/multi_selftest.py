#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线自检：多用户配置、管理员哈希、调度状态。"""
import json
import os
import pathlib
import re
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / 'app' / 'scripts'
BASE = pathlib.Path(tempfile.mkdtemp(prefix='rvtu-multi-selftest-'))
os.environ.update({
    'RVTU_STATE_DIR': str(BASE / 'state'),
    'STATE_DIR': str(BASE / 'state'),
    'RVTU_USERS_DIR': str(BASE / 'state' / 'users'),
    'RVTU_TEMPLATE_DIR': str(REPO / 'defaults' / 'user-template'),
    'DEFAULTS_DIR': str(REPO / 'defaults'),
    'PYTHONIOENCODING': 'utf-8',
})
sys.path.insert(0, str(REPO / 'app'))
sys.path.insert(0, str(SCRIPTS))

import profiles
import multi_scheduler
from configutil import load_schedule

VALID = {
    'creds': {'user': 'fake-id', 'pass': 'fake-pass'},
    'mail': {},
    'macs': {'macs': ['AA:BB:CC:DD:EE:01']},
    'location': {'latitude': '35.1', 'longitude': '119.1',
                 'locationInfo': 'test'},
    'portal': {'umac': '11:22:33:44:55:66', 'uplinkMac': '',
               'allowedHosts': ['127.0.0.1'], 'allowedSchemes': ['http'],
               'allowAny': False, 'macFormat': 'auto'},
}


def main() -> int:
    checks = {}
    created = profiles.ensure_default_profile()
    default = profiles.get_profile('default')
    checks['default'] = (created is not None and default['slug'] == 'default'
                         and not default['enabled'])

    profiles.create_profile('a', 'a', enabled=True)
    profiles.write_config('a', VALID)
    profiles.create_profile('b', 'b', enabled=True)
    profiles.write_config('b', VALID)
    errors, _warnings = profiles.validate_dir(profiles.config_dir('a'))
    checks['profile_ok'] = not errors and profiles.get_profile('a')['enabled']

    profiles.save_meta('a', tasks={'checkin': True, 'happy': False,
                                   'portal': True})
    all_profiles = profiles.list_profiles(validate=True)
    happy_users = [p['slug'] for p in
                   multi_scheduler.task_profiles(all_profiles, 'happy')]
    checks['task_filter'] = 'a' not in happy_users and 'b' in happy_users
    bad_location = dict(VALID)
    bad_location['location'] = {'latitude': '35.1', 'longitude': '119.1',
                                'locationInfo': '<打卡点地点描述>'}
    label_errors, _label_warnings = profiles.validate_data(bad_location)
    checks['error_labels'] = (any('地点描述' in e for e in label_errors)
                              and not any('location.' in e
                                          for e in label_errors))
    partial = {'creds': VALID['creds'], 'mail': {},
               'macs': {'macs': []}, 'location': {}, 'portal': {}}
    task_errors, _task_warnings = profiles.validate_data(
        partial, {'checkin': True, 'happy': False, 'portal': False})
    checks['task_validation'] = not task_errors

    admin = profiles.ensure_admin()
    again = profiles.ensure_admin()
    checks['admin_created'] = bool(admin) and again is None
    checks['admin_verify'] = bool(admin) and profiles.verify_admin(
        admin[0], admin[1]) and not profiles.verify_admin(admin[0], 'wrong')
    checks['admin_shape'] = bool(admin) and (
        len(admin[0]) == 6 and re.fullmatch(r'[a-z0-9]{6}', admin[0]) is not None
        and len(admin[1]) == 20
        and re.search(r'[a-z]', admin[1]) is not None
        and re.search(r'[A-Z]', admin[1]) is not None
        and re.search(r'[0-9]', admin[1]) is not None
        and re.search(r'[^A-Za-z0-9]', admin[1]) is not None)

    sched = load_schedule(str(REPO / 'defaults' / 'schedule.json'))
    order = multi_scheduler.random_order(['a', 'b'])
    checks['gaps'] = sched['multi'] == {'launch_gap_min_sec': 1,
                                        'launch_gap_max_sec': 10}
    checks['order'] = sorted(order) == ['a', 'b']

    store = multi_scheduler.StateStore(str(BASE / 'state' / 'multi_state.json'))
    store.load('2026-10-26')
    store.queue_task('a', 'checkin')
    store.start_task('a', 'checkin', 1)
    store.finish_task('a', 'checkin', 0, 20, '2026-10-26')
    store.queue_task('b', 'checkin')
    store.start_task('b', 'checkin', 1)
    store.finish_task('b', 'checkin', 2, 20, '2026-10-26')
    store.queue_task('c', 'checkin')
    store.recovery()
    checks['done'] = store.task('a', 'checkin').get('status') == 'done'
    checks['retry'] = store.task('b', 'checkin').get('status') == 'retry_pending'
    checks['recover'] = store.task('c', 'checkin').get('status') == 'retry_pending'

    profiles.delete_profile('a')
    checks['delete'] = ('a' not in [p['slug'] for p in profiles.list_profiles()]
                        and (profiles.USERS_DIR / '.trash').is_dir())

    print(json.dumps(checks, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
