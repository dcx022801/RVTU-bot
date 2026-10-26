#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RVTU-bot Windows 图形界面

功能：
    - 复用 app/scheduler.py 与 app/scripts 下的核心逻辑（打卡 / 晚自习 / 校园网认证）
    - 托盘运行、关闭窗口最小化到托盘
    - 开机自启（写入当前用户注册表 Run 项）
    - 启动 / 停止 / 重启调度，查看任务状态、心跳和实时日志
    - 配置检查（缺少凭据、占位符、白名单为空等）

运行：
    win\\install.bat        安装 pystray / Pillow
    win\\start.bat          启动（无控制台窗口）
    python win\\gui.py       调试启动（有控制台）

依赖：Python 3.10+（含 tkinter）、pystray、Pillow。
"""
import ctypes
import datetime
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

FROZEN = bool(getattr(sys, 'frozen', False))
DATA_ROOT = (Path(sys.executable).resolve().parent if FROZEN
             else Path(__file__).resolve().parents[1])
RESOURCE_ROOT = (Path(getattr(sys, '_MEIPASS', DATA_ROOT))
                 if FROZEN else DATA_ROOT)
ROOT = DATA_ROOT
APP_DIR = RESOURCE_ROOT / 'app'
SCRIPTS_DIR = APP_DIR / 'scripts'
TEMPLATE_CONFIG_DIR = RESOURCE_ROOT / 'config'
STATE_DIR = Path(os.environ.get('RVTU_STATE_DIR')
                 or os.environ.get('STATE_DIR')
                 or (DATA_ROOT / 'state')).expanduser()
CONFIG_DIR = Path(os.environ.get('RVTU_CONFIG_DIR')
                  or os.environ.get('CONFIG_DIR')
                  or (STATE_DIR / 'config')).expanduser()
_DEFAULTS_ROOT = RESOURCE_ROOT if FROZEN else DATA_ROOT
DEFAULTS_DIR = Path(os.environ.get('RVTU_DEFAULTS_DIR')
                    or os.environ.get('DEFAULTS_DIR')
                    or (_DEFAULTS_ROOT / 'defaults')).expanduser()
LOG_DIR = STATE_DIR / 'logs'
DRIVER_LOG = LOG_DIR / 'driver.log'
GUI_PATH = Path(sys.executable).resolve() if FROZEN else Path(__file__).resolve()
VENV_PYTHON = (None if FROZEN
               else DATA_ROOT / 'win' / '.venv' / 'Scripts' / 'python.exe')
VENV_PYTHONW = (None if FROZEN
                else DATA_ROOT / 'win' / '.venv' / 'Scripts' / 'pythonw.exe')
# Windows 默认使用 state/config（已被 .gitignore 忽略），避免把真实凭据写进被跟踪的 config/ 模板
SEED_CONFIG = CONFIG_DIR == (STATE_DIR / 'config')

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from configutil import (ConfigError, load_api, load_creds, load_location,
                        load_macs, load_mail, load_portal, load_schedule)
from logutil import append_log

AUTOSTART_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
AUTOSTART_NAME = 'RVTU-bot'
CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
FONT = 'Microsoft YaHei UI'
STATUS_TEXT = {
    '': '待执行',
    'running': '执行中',
    'done': '已完成',
    'retry_pending': '等待重试',
    'failed': '失败',
    'skipped': '已跳过',
}
_mutex_handle = None


def show_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, 'RVTU-bot', 0x10)
    except Exception:
        print(message, file=sys.stderr)


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def prepare_tk_environment() -> None:
    """部分 Windows Python 安装（含虚拟环境）找不到 Tcl/Tk 数据目录，这里补一次搜索。"""
    if FROZEN:
        return
    if os.environ.get('TCL_LIBRARY') and os.environ.get('TK_LIBRARY'):
        if (Path(os.environ['TCL_LIBRARY']) / 'init.tcl').exists() and \
                (Path(os.environ['TK_LIBRARY']) / 'tk.tcl').exists():
            return
    bases = [Path(sys.base_prefix), Path(sys.prefix), Path(sys.executable).parent]
    local = os.environ.get('LOCALAPPDATA')
    if local:
        py_root = Path(local) / 'Programs' / 'Python'
        if py_root.is_dir():
            bases.extend(sorted(py_root.glob('Python*/')))
    for base in bases:
        tcl_root = base / 'tcl'
        if not tcl_root.is_dir():
            continue
        for tcl in sorted(tcl_root.glob('tcl8.*'), reverse=True):
            version = tcl.name[3:]
            tk = tcl_root / f'tk{version}'
            if (tcl / 'init.tcl').exists() and (tk / 'tk.tcl').exists():
                os.environ['TCL_LIBRARY'] = str(tcl)
                os.environ['TK_LIBRARY'] = str(tk)
                return


def acquire_single_instance() -> bool:
    """Windows 命名互斥量：避免开机自启与手动启动同时运行两个 GUI。"""
    global _mutex_handle
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        _mutex_handle = kernel32.CreateMutexW(None, False, 'Local\\RVTU-bot-GUI')
        if not _mutex_handle:
            return True
        if kernel32.GetLastError() == 183:
            kernel32.CloseHandle(_mutex_handle)
            _mutex_handle = None
            return False
        return True
    except Exception:
        return True


def scheduler_python() -> str:
    """调度器需要可写 stdout 的 python.exe；优先使用项目虚拟环境。"""
    if FROZEN:
        return sys.executable
    if VENV_PYTHON and VENV_PYTHON.exists():
        return str(VENV_PYTHON)
    exe = Path(sys.executable)
    if exe.name.lower() == 'pythonw.exe':
        candidate = exe.with_name('python.exe')
        if candidate.exists():
            return str(candidate)
    return str(exe)


def scheduler_command() -> list:
    """GUI 启动调度器的命令：源码模式用 Python，exe 模式用自身分发参数。"""
    if FROZEN:
        return [sys.executable, '--run-scheduler']
    return [scheduler_python(), '-u', str(APP_DIR / 'scheduler.py')]


def load_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        try:
            result = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}', '/NH'],
                capture_output=True, text=True, timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            return str(pid) in (result.stdout or '')
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_is_scheduler(pid: int) -> bool:
    """确认 PID 对应的命令行确实是 scheduler.py，避免误杀复用的 PID。"""
    if os.name == 'nt':
        try:
            command = (f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\")"
                       ".CommandLine")
            result = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', command],
                capture_output=True, text=True, timeout=8,
                creationflags=CREATE_NO_WINDOW,
            )
            return 'scheduler.py' in (result.stdout or '')
        except Exception:
            return False
    try:
        raw = Path(f'/proc/{pid}/cmdline').read_bytes()
        return 'scheduler.py' in raw.replace(b'\0', b' ').decode('utf-8', 'ignore')
    except Exception:
        return False


def kill_tree(pid: int) -> None:
    if pid <= 0:
        return
    if os.name == 'nt':
        try:
            subprocess.run(
                ['taskkill', '/PID', str(pid), '/T', '/F'],
                capture_output=True, timeout=10,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception:
            pass
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def autostart_command() -> str:
    if FROZEN:
        exe = Path(sys.executable)
        args = [str(exe), '--autostart']
    else:
        if VENV_PYTHONW and VENV_PYTHONW.exists():
            exe = VENV_PYTHONW
        else:
            exe = Path(sys.executable)
            if exe.name.lower() != 'pythonw.exe':
                candidate = exe.with_name('pythonw.exe')
                if candidate.exists():
                    exe = candidate
        args = [str(exe), str(GUI_PATH), '--autostart']
    return subprocess.list2cmdline(args)


def autostart_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_autostart(enabled: bool) -> None:
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass


class RvtuApp:
    def __init__(self, pystray, Image, ImageDraw, ImageTk, autostart_mode: bool):
        self.pystray = pystray
        self.Image = Image
        self.ImageDraw = ImageDraw
        self.ImageTk = ImageTk
        self.autostart_mode = autostart_mode
        self.proc = None
        self.icon = None
        self._tray_running = None
        self._notified_hide = False
        self._quitting = False
        self._tick = 0
        self._driver_pos = 0
        self.queue = queue.Queue()
        self.actions = queue.Queue()

        prepare_tk_environment()
        self.root = tk.Tk()
        self.root.title('RVTU-bot 控制台')
        self.root.minsize(820, 540)
        self.root.report_callback_exception = self._report_callback_exception
        self.status_var = tk.StringVar(value='调度器：已停止')
        self.clock_var = tk.StringVar(value='')
        self.heartbeat_var = tk.StringVar(value='心跳：无')
        self.task_vars = {name: tk.StringVar(value='读取中…')
                          for name in ('checkin', 'happy', 'portal')}
        self.autostart_var = tk.BooleanVar(value=autostart_enabled())

        self._build_ui()
        self._set_window_icon()
        self._center_window()

        if self.autostart_mode:
            self.root.withdraw()
        self.root.after(200, self._bootstrap)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(3, weight=1)

        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label='打开配置目录', command=self.open_config_dir)
        file_menu.add_command(label='打开日志目录', command=self.open_log_dir)
        file_menu.add_separator()
        file_menu.add_command(label='隐藏到系统托盘', command=self.hide_to_tray)
        file_menu.add_command(label='退出', command=self.quit_app)
        menubar.add_cascade(label='文件', menu=file_menu)

        action_menu = tk.Menu(menubar, tearoff=0)
        action_menu.add_command(label='启动调度', command=self.start_scheduler)
        action_menu.add_command(label='停止调度', command=self.stop_scheduler)
        action_menu.add_command(label='重启调度', command=self.restart_scheduler)
        action_menu.add_separator()
        action_menu.add_command(label='检查配置', command=self.check_config_ui)
        menubar.add_cascade(label='操作', menu=action_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label='关于', command=self._show_about)
        menubar.add_cascade(label='帮助', menu=help_menu)
        self.root.config(menu=menubar)

        header = ttk.Frame(self.root, padding=(14, 12, 14, 6))
        header.grid(row=0, column=0, sticky='ew')
        ttk.Label(header, text='RVTU-bot 控制台',
                  font=(FONT, 18, 'bold')).pack(side='left')
        self.status_label = tk.Label(header, textvariable=self.status_var,
                                     font=(FONT, 11, 'bold'), fg='#a40e26')
        self.status_label.pack(side='right')

        toolbar = ttk.Frame(self.root, padding=(14, 0, 14, 8))
        toolbar.grid(row=1, column=0, sticky='ew')
        ttk.Button(toolbar, text='启动调度', command=self.start_scheduler).pack(side='left')
        ttk.Button(toolbar, text='停止调度', command=self.stop_scheduler).pack(side='left', padx=(8, 0))
        ttk.Button(toolbar, text='重启调度', command=self.restart_scheduler).pack(side='left', padx=(8, 0))
        ttk.Button(toolbar, text='检查配置', command=self.check_config_ui).pack(side='left', padx=(8, 0))
        ttk.Button(toolbar, text='隐藏到托盘', command=self.hide_to_tray).pack(side='right')

        info = ttk.LabelFrame(self.root, text='运行状态', padding=10)
        info.grid(row=2, column=0, sticky='ew', padx=14, pady=(0, 8))
        info.columnconfigure(0, weight=1)
        for row, name in enumerate(('checkin', 'happy', 'portal')):
            ttk.Label(info, textvariable=self.task_vars[name],
                      font=(FONT, 10)).grid(row=row, column=0, columnspan=2, sticky='w', pady=2)
        ttk.Label(info, textvariable=self.heartbeat_var,
                  font=(FONT, 9)).grid(row=3, column=0, sticky='w', pady=(6, 0))
        ttk.Label(info, textvariable=self.clock_var,
                  font=(FONT, 9)).grid(row=3, column=1, sticky='e', pady=(6, 0))

        log_frame = ttk.LabelFrame(self.root, text='运行日志', padding=8)
        log_frame.grid(row=3, column=0, sticky='nsew', padx=14, pady=(0, 8))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=14, wrap='word', state='disabled',
                                font=(FONT, 9), relief='flat', borderwidth=1,
                                background='#ffffff', foreground='#1f2328')
        scroll = ttk.Scrollbar(log_frame, orient='vertical', command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')

        footer = ttk.Frame(self.root, padding=(14, 0, 14, 12))
        footer.grid(row=4, column=0, sticky='ew')
        ttk.Checkbutton(footer, text='开机自启', variable=self.autostart_var,
                        command=self._toggle_autostart).pack(side='left')
        ttk.Button(footer, text='打开配置目录',
                   command=self.open_config_dir).pack(side='left', padx=(18, 0))
        ttk.Button(footer, text='打开日志目录',
                   command=self.open_log_dir).pack(side='left', padx=(8, 0))
        ttk.Button(footer, text='退出程序',
                   command=self.quit_app).pack(side='right')

        self.root.protocol('WM_DELETE_WINDOW', self.hide_to_tray)
        self.root.bind('<Unmap>', self._on_unmap)

    def _set_window_icon(self) -> None:
        if not self.ImageTk:
            return
        try:
            self._window_icon = self.ImageTk.PhotoImage(self._make_image(False))
            self.root.iconphoto(True, self._window_icon)
        except Exception:
            self._window_icon = None

    def _center_window(self) -> None:
        width, height = 940, 620
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2)
        self.root.geometry(f'{width}x{height}+{x}+{y}')

    def _ensure_config_dir(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            if not SEED_CONFIG:
                return
            for name in ('creds.json', 'mail.json', 'macs.json',
                         'location.json', 'portal.json'):
                target = CONFIG_DIR / name
                source = TEMPLATE_CONFIG_DIR / name
                if not target.exists() and source.exists():
                    shutil.copyfile(source, target)
        except Exception as exc:
            self._emit(f'[GUI] 初始化配置目录失败：{exc}', persist=True)

    def _bootstrap(self) -> None:
        self._ensure_config_dir()
        self._init_tray()
        if self.autostart_mode and not self.icon:
            self.show_window()
        self._load_recent_log()
        self._emit('[GUI] RVTU-bot Windows 版已启动', persist=True)
        self._refresh_view()
        self.start_scheduler(interactive=not self.autostart_mode)
        self.root.after(500, self._periodic)

    def _init_tray(self) -> None:
        try:
            menu = self.pystray.Menu(
                self.pystray.MenuItem('显示主窗口', self._tray_show, default=True),
                self.pystray.MenuItem('启动调度', self._tray_start),
                self.pystray.MenuItem('停止调度', self._tray_stop),
                self.pystray.Menu.SEPARATOR,
                self.pystray.MenuItem('退出', self._tray_quit),
            )
            self.icon = self.pystray.Icon(
                'rvtu-bot', self._make_image(False),
                'RVTU-bot（调度已停止）', menu)
            try:
                self.icon.run_detached()
            except Exception:
                threading.Thread(target=self.icon.run, daemon=True).start()
            self._emit('[GUI] 系统托盘已启用', persist=True)
        except Exception as exc:
            self.icon = None
            self._emit(f'[GUI] 系统托盘初始化失败：{exc}', persist=True)
            if FROZEN:
                show_error(f'系统托盘初始化失败：{exc}\n\n'
                           f'请确认程序完整解压后运行。')
            else:
                show_error(f'系统托盘初始化失败：{exc}\n\n'
                           f'请先运行 win\\install.bat 安装 pystray / Pillow。')

    def _make_image(self, running: bool):
        image = self.Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = self.ImageDraw.Draw(image)
        color = (34, 139, 34, 255) if running else (120, 120, 120, 255)
        draw.ellipse((4, 4, 60, 60), fill=color, outline=(255, 255, 255, 255), width=2)
        if running:
            draw.polygon(((26, 19), (47, 32), (26, 45)), fill=(255, 255, 255, 255))
        else:
            draw.rectangle((23, 23, 41, 41), fill=(255, 255, 255, 255))
        return image

    def _report_callback_exception(self, exc, value, traceback) -> None:
        message = f'{exc.__name__}: {value}'
        self._emit(f'[GUI] 未处理异常：{message}', persist=True)
        if not self._quitting:
            try:
                messagebox.showerror('RVTU-bot 异常', message, parent=self.root)
            except Exception:
                pass

    def _on_unmap(self, event) -> None:
        try:
            if event.widget is self.root and self.root.state() == 'iconic':
                self.root.after(1, self.hide_to_tray)
        except tk.TclError:
            pass

    def _load_recent_log(self) -> None:
        path = DRIVER_LOG if FROZEN else LOG_DIR / 'scheduler.log'
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
            for line in text.splitlines()[-200:]:
                self._append_text(line)
            if text:
                self._append_text('')
            if FROZEN:
                try:
                    self._driver_pos = path.stat().st_size
                except Exception:
                    self._driver_pos = 0
        except Exception:
            pass

    def _tail_driver_log(self) -> None:
        """exe 模式的调度器/任务 stdout 写入 driver.log，这里增量读取。"""
        if not FROZEN:
            return
        try:
            size = DRIVER_LOG.stat().st_size
        except Exception:
            return
        if size < self._driver_pos:
            self._driver_pos = 0
        if size == self._driver_pos:
            return
        try:
            with open(DRIVER_LOG, 'r', encoding='utf-8', errors='replace') as handle:
                handle.seek(self._driver_pos)
                chunk = handle.read()
                self._driver_pos = handle.tell()
            for line in chunk.splitlines():
                self._append_text(line)
        except Exception:
            pass

    def _append_text(self, line: str) -> None:
        try:
            self.log_text.configure(state='normal')
            self.log_text.insert('end', line + '\n')
            total = int(self.log_text.index('end-1c').split('.')[0])
            if total > 4000:
                self.log_text.delete('1.0', '1000.0')
            self.log_text.see('end')
            self.log_text.configure(state='disabled')
        except tk.TclError:
            pass

    def _emit(self, message: str, persist: bool = False) -> None:
        line = f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {message}'
        self.queue.put(line)
        if persist:
            append_log(str(LOG_DIR / 'gui.log'), line)

    def _drain_queue(self) -> None:
        for _ in range(500):
            try:
                line = self.queue.get_nowait()
            except queue.Empty:
                break
            self._append_text(line)

    def _drain_actions(self) -> None:
        if self._quitting:
            return
        for _ in range(20):
            try:
                action = self.actions.get_nowait()
            except queue.Empty:
                break
            if action == 'show':
                self.show_window()
            elif action == 'start':
                self.start_scheduler()
            elif action == 'stop':
                self.stop_scheduler()
            elif action == 'quit':
                self.quit_app()
                return
            if self._quitting:
                return

    def _periodic(self) -> None:
        if self._quitting:
            return
        self._drain_actions()
        self._drain_queue()
        self._tail_driver_log()
        self._tick += 1
        if self._tick % 2 == 0:
            self._refresh_view()
        if not self._quitting:
            try:
                self.root.after(500, self._periodic)
            except tk.TclError:
                pass

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _pump_output(self, proc) -> None:
        try:
            if proc.stdout is None:
                return
            for line in iter(proc.stdout.readline, ''):
                if line:
                    self.queue.put(line.rstrip('\r\n'))
        except Exception as exc:
            self.queue.put(f'[GUI] 读取调度器输出失败：{exc}')
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    def _cleanup_orphan(self) -> None:
        if self.is_running():
            return
        path = STATE_DIR / 'scheduler.pid'
        try:
            pid = int(path.read_text(encoding='utf-8').strip())
        except Exception:
            return
        if pid <= 0 or pid == os.getpid() or not pid_alive(pid):
            try:
                path.unlink()
            except Exception:
                pass
            return
        if not pid_is_scheduler(pid):
            self._emit(f'[GUI] scheduler.pid 指向的进程 {pid} 不是调度器，忽略该文件',
                       persist=True)
            try:
                path.unlink()
            except Exception:
                pass
            return
        self._emit(f'[GUI] 发现未接管的调度器（PID {pid}），正在停止...', persist=True)
        kill_tree(pid)
        for _ in range(30):
            if not pid_alive(pid):
                break
            time.sleep(0.1)
        try:
            path.unlink()
        except Exception:
            pass

    def start_scheduler(self, interactive: bool = True) -> None:
        if self._quitting:
            return
        if self.is_running():
            self._emit('[GUI] 调度器已在运行')
            return
        if self.proc is not None:
            self._emit(f'[GUI] 上次调度器已退出（退出码 {self.proc.returncode}）',
                       persist=True)
            self.proc = None
        self._cleanup_orphan()

        errors, warnings = self._check_config()
        for warning in warnings:
            self._emit(f'[GUI] 配置提示：{warning}', persist=True)
        if errors:
            for error in errors:
                self._emit(f'[GUI] 配置错误：{error}', persist=True)
            if not interactive:
                self._emit('[GUI] 配置未通过校验，调度器未启动', persist=True)
                return
            detail = '\n'.join(errors[:8])
            if not messagebox.askyesno(
                    '配置未完成',
                    f'以下配置存在问题：\n\n{detail}\n\n仍要启动调度器吗？',
                    parent=self.root):
                return

        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if FROZEN and DRIVER_LOG.exists() and DRIVER_LOG.stat().st_size >= 5 * 1024 * 1024:
                try:
                    DRIVER_LOG.replace(DRIVER_LOG.with_name('driver.log.1'))
                except Exception:
                    pass
            env = os.environ.copy()
            env.update({
                'CONFIG_DIR': str(CONFIG_DIR),
                'DEFAULTS_DIR': str(DEFAULTS_DIR),
                'STATE_DIR': str(STATE_DIR),
                'PYTHONIOENCODING': 'utf-8',
                'PYTHONUNBUFFERED': '1',
                'PYTHONUTF8': '1',
            })
            self.proc = subprocess.Popen(
                scheduler_command(),
                cwd=str(ROOT), env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace', bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as exc:
            self._emit(f'[GUI] 启动调度器失败：{exc}', persist=True)
            if interactive:
                messagebox.showerror('启动失败', str(exc), parent=self.root)
            return

        self._emit(f'[GUI] 调度器已启动（PID {self.proc.pid}）', persist=True)
        threading.Thread(target=self._pump_output, args=(self.proc,), daemon=True).start()
        self._refresh_view()

    def stop_scheduler(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            self._emit('[GUI] 调度器未运行')
        else:
            if proc.poll() is not None:
                self._emit(f'[GUI] 调度器已退出（退出码 {proc.returncode}）', persist=True)
            else:
                self._emit(f'[GUI] 正在停止调度器（PID {proc.pid}）...', persist=True)
                kill_tree(proc.pid)
                try:
                    proc.wait(timeout=8)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                self._emit('[GUI] 调度器已停止', persist=True)
            try:
                (STATE_DIR / 'scheduler.pid').unlink()
            except Exception:
                pass
        self._refresh_view()

    def restart_scheduler(self) -> None:
        self.stop_scheduler()
        self.root.after(300, self.start_scheduler)

    def _check_config(self):
        errors = []
        warnings = []
        try:
            load_creds(str(CONFIG_DIR / 'creds.json'))
        except Exception as exc:
            errors.append(f'creds.json：{exc}')
        try:
            load_macs(str(CONFIG_DIR / 'macs.json'))
        except Exception as exc:
            errors.append(f'macs.json：{exc}')
        try:
            load_location(str(CONFIG_DIR / 'location.json'))
        except Exception as exc:
            errors.append(f'location.json：{exc}')
        try:
            sched = load_schedule(str(DEFAULTS_DIR / 'schedule.json'), required=True)
            load_api(str(DEFAULTS_DIR / 'api.json'), required=True)
            for warning in sched.get('_warnings', []):
                warnings.append(warning)
        except Exception as exc:
            errors.append(f'defaults：{exc}')
        try:
            portal = load_portal(str(CONFIG_DIR / 'portal.json'))
            if not portal['allowedHosts'] and not portal['allowAny']:
                warnings.append('portal.allowedHosts 为空，校园网认证会拒绝提交凭据'
                                '（fail-closed）')
        except Exception as exc:
            errors.append(f'portal.json：{exc}')
        try:
            mail = load_mail(str(CONFIG_DIR / 'mail.json'))
            if mail is None:
                warnings.append('未启用邮件通知（mail.json 为空或仍是占位符）')
        except Exception as exc:
            warnings.append(f'邮件配置无效：{exc}')
        return errors, warnings

    def check_config_ui(self) -> None:
        errors, warnings = self._check_config()
        for error in errors:
            self._emit(f'[GUI] 配置错误：{error}', persist=True)
        for warning in warnings:
            self._emit(f'[GUI] 配置提示：{warning}', persist=True)
        if errors:
            text = '发现以下错误：\n\n' + '\n'.join(f'· {item}' for item in errors)
            if warnings:
                text += '\n\n提示：\n\n' + '\n'.join(f'· {item}' for item in warnings)
            messagebox.showerror('配置检查', text, parent=self.root)
        elif warnings:
            text = '配置可用，但有以下提示：\n\n' + '\n'.join(f'· {item}' for item in warnings)
            messagebox.showwarning('配置检查', text, parent=self.root)
        else:
            messagebox.showinfo('配置检查', '配置检查通过。', parent=self.root)

    def _refresh_view(self) -> None:
        if self._quitting:
            return
        proc = self.proc
        if proc is not None and proc.poll() is not None:
            code = proc.returncode
            self.proc = None
            self._emit(f'[GUI] 调度器已退出（退出码 {code}）', persist=True)

        running = self.is_running()
        if running:
            self.status_var.set(f'调度器：运行中（PID {self.proc.pid}）')
            self.status_label.configure(fg='#1a7f37')
        else:
            self.status_var.set('调度器：已停止')
            self.status_label.configure(fg='#a40e26')

        today = datetime.date.today().isoformat()
        state = load_json(STATE_DIR / 'scheduler_state.json')
        tasks = state.get('tasks') if state.get('date') == today else {}
        if not isinstance(tasks, dict):
            tasks = {}

        try:
            sched = load_schedule(str(DEFAULTS_DIR / 'schedule.json'), required=True)
        except ConfigError:
            sched = None

        if sched:
            for name, label in (('checkin', '宿舍考勤'), ('happy', '晚自习')):
                item = sched[name]
                current = tasks.get(name) or {}
                status = STATUS_TEXT.get(current.get('status') or '', '待执行')
                self.task_vars[name].set(
                    f'{label}  {item["hour"]:02d}:{item["minute"]:02d}  ·  {status}')
            interval = sched['portal']['interval_minutes']
            self.task_vars['portal'].set(
                f'校园网认证  每 {interval} 分钟  ·  '
                + ('随调度运行' if running else '调度已停止'))
        else:
            for name in self.task_vars:
                self.task_vars[name].set('调度配置不可用')

        try:
            age = max(0, int(time.time() - (STATE_DIR / 'heartbeat').stat().st_mtime))
            heartbeat = f'心跳：{age} 秒前'
            if running and age > 180:
                heartbeat += '（异常）'
        except Exception:
            heartbeat = '心跳：无'
        self.heartbeat_var.set(heartbeat)
        self.clock_var.set(datetime.datetime.now().strftime('当前时间：%Y-%m-%d %H:%M:%S'))
        self._refresh_tray_state(running)

    def _refresh_tray_state(self, running: bool) -> None:
        if running == self._tray_running:
            return
        self._tray_running = running
        if not self.icon or self._quitting:
            return
        try:
            self.icon.icon = self._make_image(running)
            self.icon.title = ('RVTU-bot（调度运行中）' if running
                               else 'RVTU-bot（调度已停止）')
        except Exception:
            pass

    def show_window(self) -> None:
        try:
            self.root.deiconify()
            self.root.state('normal')
            self.root.lift()
            self.root.attributes('-topmost', True)
            self.root.after(150, lambda: self.root.attributes('-topmost', False))
            self.root.focus_force()
        except tk.TclError:
            pass

    def hide_to_tray(self) -> None:
        if self._quitting:
            return
        if not self.icon:
            self._emit('[GUI] 系统托盘不可用，窗口无法隐藏', persist=True)
            return
        try:
            self.root.withdraw()
        except tk.TclError:
            return
        if not self._notified_hide:
            self._notified_hide = True
            try:
                self.icon.notify('RVTU-bot 已最小化到系统托盘，双击图标可重新打开。',
                                 'RVTU-bot')
            except Exception:
                pass

    def open_config_dir(self) -> None:
        self._open_dir(CONFIG_DIR, '配置目录')

    def open_log_dir(self) -> None:
        self._open_dir(LOG_DIR, '日志目录')

    def _open_dir(self, path: Path, label: str) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
        except Exception as exc:
            messagebox.showerror(f'打开{label}失败', str(exc), parent=self.root)

    def _toggle_autostart(self) -> None:
        enabled = self.autostart_var.get()
        try:
            set_autostart(enabled)
        except Exception as exc:
            self.autostart_var.set(not enabled)
            self._emit(f'[GUI] 设置开机自启失败：{exc}', persist=True)
            messagebox.showerror('开机自启', f'设置失败：{exc}', parent=self.root)
            return
        self._emit(f'[GUI] 已{"启用" if enabled else "取消"}开机自启', persist=True)

    def _show_about(self) -> None:
        messagebox.showinfo(
            '关于 RVTU-bot',
            f'RVTU-bot Windows 控制台\n\n'
            f'项目目录：{ROOT}\n配置目录：{CONFIG_DIR}\n状态目录：{STATE_DIR}\n\n'
            f'关闭窗口会最小化到系统托盘；退出请使用托盘菜单或“退出程序”。',
            parent=self.root)

    def _tray_show(self, icon=None, item=None) -> None:
        self.actions.put('show')

    def _tray_start(self, icon=None, item=None) -> None:
        self.actions.put('start')

    def _tray_stop(self, icon=None, item=None) -> None:
        self.actions.put('stop')

    def _tray_quit(self, icon=None, item=None) -> None:
        self.actions.put('quit')

    def quit_app(self, confirm: bool = True) -> None:
        if self._quitting:
            return
        if confirm and self.is_running():
            if not messagebox.askyesno(
                    '退出 RVTU-bot',
                    '退出将停止自动打卡调度，确定退出吗？',
                    parent=self.root):
                return
        self._quitting = True
        self._emit('[GUI] 正在退出...', persist=True)
        self.stop_scheduler()
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
        self.icon = None
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self) -> None:
        self.root.mainloop()


CORE_SCRIPTS = frozenset({
    'checkin.py', 'happy_checkin.py', 'portal_auth.py',
    'notify.py', 'refresh_creds.py',
})


def _redirect_driver_output():
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handle = open(DRIVER_LOG, 'a', encoding='utf-8', buffering=1)
        sys.stdout = handle
        sys.stderr = handle
        return handle
    except Exception:
        try:
            handle = open(os.devnull, 'w', encoding='utf-8')
        except Exception:
            handle = None
        sys.stdout = handle
        sys.stderr = handle
        return handle


def run_scheduler_mode() -> int:
    """exe 模式：在后台调度器进程中运行 app/scheduler.py。"""
    _redirect_driver_output()
    for path in (str(APP_DIR), str(SCRIPTS_DIR)):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        import scheduler
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    try:
        return int(scheduler.main() or 0)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code) if isinstance(code, int) else 1
    except Exception:
        import traceback
        traceback.print_exc()
        return 1


def run_core_mode() -> int:
    """exe 模式：按调度器请求执行内置核心脚本（打卡 / 预检 / Portal 等）。"""
    if len(sys.argv) < 3:
        print('用法：RVTU-bot.exe --run-core <script> [args...]', file=sys.stderr)
        return 2
    try:
        script = Path(sys.argv[2]).resolve()
        scripts_dir = SCRIPTS_DIR.resolve()
    except Exception:
        return 2
    if script.name not in CORE_SCRIPTS or script.parent != scripts_dir:
        print('--run-core 只允许执行内置核心脚本', file=sys.stderr)
        return 2
    _redirect_driver_output()
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    sys.argv = [str(script), *sys.argv[3:]]
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('__main__', str(script))
        if spec is None or spec.loader is None:
            return 2
        module = importlib.util.module_from_spec(spec)
        sys.modules['__main__'] = module
        spec.loader.exec_module(module)
    except SystemExit as exc:
        code = exc.code
        return 0 if code is None else int(code) if isinstance(code, int) else 2
    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    return 0


def main() -> int:
    if '--run-scheduler' in sys.argv:
        return run_scheduler_mode()
    if '--run-core' in sys.argv:
        return run_core_mode()
    if os.name != 'nt':
        print('RVTU-bot Windows GUI 仅支持 Windows。', file=sys.stderr)
        return 1
    autostart_mode = '--autostart' in sys.argv
    if not acquire_single_instance():
        if not autostart_mode:
            show_error('RVTU-bot 已在运行，请在系统托盘中查看。')
        return 0

    try:
        import pystray
        from PIL import Image, ImageDraw
        try:
            from PIL import ImageTk
        except Exception:
            ImageTk = None
    except Exception as exc:
        if FROZEN:
            show_error('程序文件不完整或缺少组件：\n'
                       f'{type(exc).__name__}: {exc}\n\n'
                       '请重新解压完整的 Windows 便携包后运行。')
        else:
            show_error('缺少 Windows GUI 依赖：\n'
                       f'{type(exc).__name__}: {exc}\n\n'
                       '请先运行 win\\install.bat，或执行：\n'
                       'py -3 -m pip install -r win\\requirements.txt')
        return 2

    enable_dpi_awareness()
    try:
        app = RvtuApp(pystray, Image, ImageDraw, ImageTk, autostart_mode)
        app.run()
    except Exception as exc:
        show_error(f'RVTU-bot 启动失败：\n{type(exc).__name__}: {exc}')
        return 3
    return 0


if __name__ == '__main__':
    sys.exit(main())
