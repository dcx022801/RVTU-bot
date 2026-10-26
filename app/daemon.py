#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Docker 入口守护：多用户调度器 + Web 管理页（11451）同进程运行。"""
import os
import signal
import sys
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(BASE, 'scripts')
for path in (BASE, SCRIPTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import profiles
import multi_scheduler
import webui


def main() -> int:
    profiles.ensure_dirs()
    profiles.ensure_default_profile()
    server = webui.create_server()
    stop = threading.Event()

    def _stop(_signum=None, _frame=None):
        stop.set()

    try:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    except Exception:
        pass

    web_thread = threading.Thread(target=webui.serve, args=(server, stop),
                                  daemon=True, name='webui')
    web_thread.start()
    try:
        return multi_scheduler.main(stop_event=stop)
    finally:
        stop.set()
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        web_thread.join(timeout=3)


if __name__ == '__main__':
    sys.exit(main())
