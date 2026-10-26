#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows 多用户配置对话框。"""
import os
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import profiles

FONT = 'Microsoft YaHei UI'


def split_list(text):
    return [x for x in str(text or '').replace(';', ',').replace('\n', ',')
            .replace(' ', ',').split(',') if x.strip()]


class ProfileManager(tk.Toplevel):
    def __init__(self, parent, on_change=None):
        super().__init__(parent)
        self.parent = parent
        self.on_change = on_change
        self.title('用户管理')
        self.geometry('880x560')
        self.minsize(720, 420)
        self.transient(parent)
        self.grab_set()
        self._build()
        self.refresh()

    def _build(self):
        top = ttk.Frame(self, padding=(12, 12, 12, 6))
        top.pack(fill='x')
        ttk.Label(top, text='用户管理', font=(FONT, 14, 'bold')).pack(side='left')
        ttk.Button(top, text='新增用户', command=self.add).pack(side='right')
        ttk.Button(top, text='刷新', command=self.refresh).pack(side='right', padx=(0, 8))

        body = ttk.Frame(self, padding=(12, 0, 12, 6))
        body.pack(fill='both', expand=True)
        columns = ('name', 'slug', 'enabled', 'status')
        self.tree = ttk.Treeview(body, columns=columns, show='headings',
                                 selectmode='browse')
        for col, text, width in (('name', '名称', 200), ('slug', '标识', 130),
                                 ('enabled', '启用', 70), ('status', '状态', 360)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor='w')
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.tree.bind('<Double-1>', lambda _e: self.edit())

        bottom = ttk.Frame(self, padding=(12, 0, 12, 12))
        bottom.pack(fill='x')
        ttk.Button(bottom, text='编辑', command=self.edit).pack(side='left')
        ttk.Button(bottom, text='启用/停用', command=self.toggle).pack(side='left', padx=(8, 0))
        ttk.Button(bottom, text='打开配置目录', command=self.open_dir).pack(side='left', padx=(8, 0))
        ttk.Button(bottom, text='删除', command=self.delete).pack(side='left', padx=(8, 0))
        ttk.Button(bottom, text='关闭', command=self.destroy).pack(side='right')

    def refresh(self):
        state = profiles.read_multi_state()
        tasks = state.get('tasks') or {}
        heartbeats = state.get('heartbeats') or {}
        selected = self.selected_slug()
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.profiles = profiles.list_profiles(validate=True)
        for profile in self.profiles:
            slug = profile['slug']
            hb = heartbeats.get(slug)
            age = '无心跳'
            if hb:
                sec = max(0, int(time.time() - hb))
                age = f'心跳 {sec}s 前' if sec < 60 else f'心跳 {sec // 60} 分钟前'
            own = tasks.get(slug) or {}
            bits = []
            for key, label in (('checkin', '考勤'), ('happy', '晚自习')):
                item = own.get(key) or {}
                bits.append(f'{label}:{item.get("status") or "待执行"}')
            status = ' '.join(bits) + f' · {age}'
            task_flags = profile.get('tasks') or {}
            off_tasks = [label for key, label in (
                ('checkin', '宿舍'), ('happy', '晚自习'), ('portal', '校园网'))
                if not task_flags.get(key, True)]
            if off_tasks:
                status = '已关闭(' + '/'.join(off_tasks) + ') · ' + status
            if profile['errors']:
                status = '配置错误 · ' + status
            elif profile['warnings']:
                status = '提示 · ' + status
            self.tree.insert('', 'end', iid=slug, values=(
                profile['name'], slug,
                '是' if profile['enabled'] else '否', status))
        if selected and selected in self.tree.get_children():
            self.tree.selection_set(selected)
        elif self.tree.get_children():
            self.tree.selection_set(self.tree.get_children()[0])

    def selected_slug(self):
        selection = self.tree.selection() if hasattr(self, 'tree') else ()
        return selection[0] if selection else ''

    def _changed(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    def add(self):
        name = simpledialog.askstring('新增用户', '显示名称（例如 a / 张三）：', parent=self)
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showerror('新增用户', '名称不能为空', parent=self)
            return
        slug = simpledialog.askstring(
            '新增用户', '用户标识（小写字母/数字/下划线/连字符）：',
            initialvalue=profiles.suggest_slug(name), parent=self)
        if slug is None:
            return
        try:
            profile = profiles.create_profile(name, slug.strip() or None,
                                             enabled=False)
        except Exception as exc:
            messagebox.showerror('新增用户', str(exc), parent=self)
            return
        self.refresh()
        self._changed()
        self._open_editor(profile['slug'])

    def edit(self):
        slug = self.selected_slug()
        if not slug:
            return
        self._open_editor(slug)

    def _open_editor(self, slug):
        ProfileEditor(self, slug, on_saved=self._saved)

    def _saved(self):
        self.refresh()
        self._changed()

    def toggle(self):
        slug = self.selected_slug()
        if not slug:
            return
        try:
            profile = profiles.get_profile(slug)
            profiles.save_meta(slug, enabled=not profile['enabled'])
            profiles.touch_reload()
        except Exception as exc:
            messagebox.showerror('切换用户', str(exc), parent=self)
            return
        self.refresh()
        self._changed()

    def open_dir(self):
        slug = self.selected_slug()
        if not slug:
            return
        try:
            os.startfile(profiles.config_dir(slug))
        except Exception as exc:
            messagebox.showerror('打开目录', str(exc), parent=self)

    def delete(self):
        slug = self.selected_slug()
        if not slug:
            return
        profile = profiles.get_profile(slug)
        if not messagebox.askyesno(
                '删除用户',
                f'删除用户 {profile["name"]}（{slug}）？\n'
                '配置目录会移入 state/users/.trash，可手动恢复。',
                parent=self):
            return
        try:
            profiles.delete_profile(slug)
            profiles.touch_reload()
        except Exception as exc:
            messagebox.showerror('删除用户', str(exc), parent=self)
            return
        self.refresh()
        self._changed()


class ProfileEditor(tk.Toplevel):
    def __init__(self, parent, slug, on_saved=None):
        super().__init__(parent)
        self.slug = slug
        self.on_saved = on_saved
        self.title(f'编辑用户 · {slug}')
        self.geometry('760x680')
        self.minsize(680, 560)
        self.transient(parent)
        self.grab_set()
        self.profile = profiles.get_profile(slug)
        self.data = profiles.read_config(slug)
        self._build()
        self._load()

    def _build(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 4))
        top.pack(fill='x')
        ttk.Label(top, text=f'用户：{self.profile["name"]}（{self.slug}）',
                  font=(FONT, 13, 'bold')).pack(side='left')
        ttk.Label(top, text='保存后约 5 秒内生效',
                  foreground='#667085').pack(side='right')

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill='both', expand=True, padx=12, pady=6)

        basic = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(basic, text='基本')
        self.var_name = tk.StringVar()
        self.var_slug = tk.StringVar(value=self.slug)
        self.var_enabled = tk.BooleanVar()
        self.var_task_checkin = tk.BooleanVar(value=True)
        self.var_task_happy = tk.BooleanVar(value=True)
        self.var_task_portal = tk.BooleanVar(value=True)
        self._label_entry(basic, '用户名称 *', self.var_name, 0)
        self._label_entry(basic, '用户标识（不可改）', self.var_slug, 1, state='disabled')
        ttk.Checkbutton(basic, text='启用调度', variable=self.var_enabled).grid(
            row=2, column=0, columnspan=2, sticky='w', pady=(10, 0))
        basic.columnconfigure(1, weight=1)

        account = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(account, text='账号')
        self.var_user = tk.StringVar()
        self.var_pass = tk.StringVar()
        self._label_entry(account, '身份证号 / 账号 *', self.var_user, 0)
        self._label_entry(account, '统一密码 *', self.var_pass, 1, show='*')
        account.columnconfigure(1, weight=1)

        dorm = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(dorm, text='宿舍打卡')
        ttk.Label(dorm, text='使用“账号”页的身份证号/密码，无需单独填写 MAC 或坐标。',
                  wraplength=620, justify='left').pack(anchor='w', pady=(0, 8))
        ttk.Label(dorm, text='打卡时间由 defaults/schedule.json 共用（默认 21:00）；'
                             '确认窗口/迟到线以学校系统为准。',
                  wraplength=620, justify='left').pack(anchor='w', pady=(0, 12))
        ttk.Checkbutton(dorm, text='启用宿舍打卡（每天 21:00 共用时间表）',
                        variable=self.var_task_checkin).pack(anchor='w')

        mail = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(mail, text='邮件')
        self.var_mail_host = tk.StringVar()
        self.var_mail_port = tk.StringVar(value='465')
        self.var_mail_user = tk.StringVar()
        self.var_mail_pass = tk.StringVar()
        self.var_mail_sender = tk.StringVar()
        self.var_mail_to = tk.StringVar()
        for index, (label, var, show) in enumerate((
                ('SMTP 主机', self.var_mail_host, None),
                ('端口', self.var_mail_port, None),
                ('邮箱账号', self.var_mail_user, None),
                ('授权码', self.var_mail_pass, '*'),
                ('发件邮箱', self.var_mail_sender, None),
                ('收件邮箱（逗号分隔）', self.var_mail_to, None))):
            self._label_entry(mail, label, var, index, show=show)
        mail.columnconfigure(1, weight=1)

        happy = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(happy, text='晚自习')
        ttk.Checkbutton(happy, text='启用晚自习打卡（每天 19:00 共用时间表）',
                        variable=self.var_task_happy).grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 10))
        ttk.Label(happy, text='晚自习 MAC 池 *（逗号/换行分隔）').grid(
            row=1, column=0, sticky='nw', pady=4)
        self.text_macs = tk.Text(happy, height=5, font=(FONT, 10), wrap='word')
        self.text_macs.grid(row=2, column=0, columnspan=2, sticky='nsew', pady=(0, 8))
        self.var_lat = tk.StringVar()
        self.var_lon = tk.StringVar()
        self.var_info = tk.StringVar()
        self._label_entry(happy, '纬度 *', self.var_lat, 3)
        self._label_entry(happy, '经度 *', self.var_lon, 4)
        self._label_entry(happy, '地点描述 *', self.var_info, 5)
        happy.columnconfigure(1, weight=1)
        happy.rowconfigure(2, weight=1)

        portal = ttk.Frame(self.notebook, padding=14)
        self.notebook.add(portal, text='校园网')
        self.var_umac = tk.StringVar()
        self.var_uplink = tk.StringVar()
        self.var_hosts = tk.StringVar()
        self.var_schemes = tk.StringVar(value='https,http')
        self.var_allow_any = tk.BooleanVar()
        self.var_mac_format = tk.StringVar(value='auto')
        ttk.Checkbutton(portal, text='启用校园网 Portal 认证（每 1 分钟共用间隔）',
                        variable=self.var_task_portal).grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 10))
        self._label_entry(portal, '被认证网卡 MAC（umac）', self.var_umac, 1)
        self._label_entry(portal, '上联设备 MAC', self.var_uplink, 2)
        self._label_entry(portal, '允许门户主机（逗号分隔，建议填写）',
                          self.var_hosts, 3)
        self._label_entry(portal, '允许协议（逗号分隔）', self.var_schemes, 4)
        ttk.Label(portal, text='MAC 分隔符').grid(row=5, column=0, sticky='w', pady=6)
        ttk.Combobox(portal, textvariable=self.var_mac_format,
                     values=('auto', 'colon', 'hyphen'),
                     state='readonly').grid(row=5, column=1, sticky='ew', pady=6)
        ttk.Checkbutton(portal, text='allowAny（跳过白名单，不安全）',
                        variable=self.var_allow_any).grid(
            row=6, column=0, columnspan=2, sticky='w', pady=(8, 0))
        portal.columnconfigure(1, weight=1)

        error_frame = ttk.Frame(self, padding=(16, 0, 16, 0))
        error_frame.pack(fill='x')
        self.error_text = tk.Text(error_frame, height=3, wrap='word',
                                  font=(FONT, 9), foreground='#a40e26',
                                  background='#fff5f5', relief='flat',
                                  borderwidth=1, state='disabled')
        error_scroll = ttk.Scrollbar(error_frame, orient='vertical',
                                     command=self.error_text.yview)
        self.error_text.configure(yscrollcommand=error_scroll.set)
        self.error_text.pack(side='left', fill='x', expand=True)
        error_scroll.pack(side='right', fill='y')
        bottom = ttk.Frame(self, padding=(12, 6, 12, 12))
        bottom.pack(fill='x')
        ttk.Button(bottom, text='取消', command=self.destroy).pack(side='right')
        ttk.Button(bottom, text='保存', command=self.save).pack(side='right', padx=(0, 8))

    def _label_entry(self, parent, label, var, row, state='normal', show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=5)
        entry = ttk.Entry(parent, textvariable=var, state=state, show=show)
        entry.grid(row=row, column=1, sticky='ew', pady=5, padx=(10, 0))
        return entry

    def _load(self):
        data = self.data
        creds = data.get('creds') or {}
        mail = data.get('mail') or {}
        macs = data.get('macs') or {}
        loc = data.get('location') or {}
        portal = data.get('portal') or {}
        self.var_name.set(self.profile['name'])
        self.var_enabled.set(bool(self.profile['enabled']))
        tasks = self.profile.get('tasks') or {}
        self.var_task_checkin.set(bool(tasks.get('checkin', True)))
        self.var_task_happy.set(bool(tasks.get('happy', True)))
        self.var_task_portal.set(bool(tasks.get('portal', True)))
        self.var_user.set(creds.get('user') or '')
        self.var_pass.set(creds.get('pass') or '')
        self.var_mail_host.set(mail.get('host') or '')
        self.var_mail_port.set(str(mail.get('port') or 465))
        self.var_mail_user.set(mail.get('user') or '')
        self.var_mail_pass.set(mail.get('password') or '')
        self.var_mail_sender.set(mail.get('sender') or '')
        to = mail.get('to') or []
        self.var_mail_to.set(','.join(to) if isinstance(to, list) else str(to))
        values = macs.get('macs') or []
        self.text_macs.delete('1.0', 'end')
        self.text_macs.insert('1.0', '\n'.join(values) if isinstance(values, list) else str(values))
        self.var_lat.set(loc.get('latitude') or '')
        self.var_lon.set(loc.get('longitude') or '')
        self.var_info.set(loc.get('locationInfo') or '')
        self.var_umac.set(portal.get('umac') or '')
        self.var_uplink.set(portal.get('uplinkMac') or '')
        hosts = portal.get('allowedHosts') or []
        self.var_hosts.set(','.join(hosts) if isinstance(hosts, list) else str(hosts))
        schemes = portal.get('allowedSchemes') or ['https', 'http']
        self.var_schemes.set(','.join(schemes) if isinstance(schemes, list) else str(schemes))
        self.var_allow_any.set(bool(portal.get('allowAny')))
        self.var_mac_format.set(portal.get('macFormat') or 'auto')

    def _set_error(self, text: str) -> None:
        self.error_text.configure(state='normal')
        self.error_text.delete('1.0', 'end')
        if text:
            self.error_text.insert('1.0', text)
            self.error_text.configure(height=min(6, max(3, text.count(chr(10)) + 2)))
            self.error_text.see('1.0')
        self.error_text.configure(state='disabled')

    def _report(self, errors, warnings):
        lines = []
        if errors:
            lines.append('配置错误：')
            lines.extend('· ' + item for item in errors)
        if warnings:
            lines.append('提示：')
            lines.extend('· ' + item for item in warnings)
        self._set_error(chr(10).join(lines))
        if errors:
            return False
        blocking = [w for w in warnings
                    if not w.startswith('未启用邮件通知')]
        if blocking:
            return messagebox.askyesno(
                '配置提示', chr(10).join(blocking) + chr(10) * 2 + '仍要保存吗？',
                parent=self)
        return True

    def save(self):
        mail_fields = (self.var_mail_host.get().strip(),
                       self.var_mail_user.get().strip(),
                       self.var_mail_pass.get().strip(),
                       self.var_mail_sender.get().strip(),
                       self.var_mail_to.get().strip())
        mail = {}
        if any(mail_fields):
            try:
                port = int(self.var_mail_port.get().strip() or '465')
            except Exception:
                port = self.var_mail_port.get().strip()
            mail = {
                'host': mail_fields[0],
                'port': port,
                'user': mail_fields[1],
                'password': mail_fields[2],
                'sender': mail_fields[3],
                'to': split_list(mail_fields[4]),
            }
        data = {
            'creds': {'user': self.var_user.get().strip(),
                      'pass': self.var_pass.get().strip()},
            'mail': mail,
            'macs': {'macs': split_list(self.text_macs.get('1.0', 'end'))},
            'location': {
                'latitude': self.var_lat.get().strip(),
                'longitude': self.var_lon.get().strip(),
                'locationInfo': self.var_info.get().strip(),
            },
            'portal': {
                'umac': self.var_umac.get().strip(),
                'uplinkMac': self.var_uplink.get().strip(),
                'allowedHosts': split_list(self.var_hosts.get()),
                'allowedSchemes': split_list(self.var_schemes.get()) or ['https', 'http'],
                'allowAny': bool(self.var_allow_any.get()),
                'macFormat': self.var_mac_format.get() or 'auto',
            },
        }
        tasks = {
            'checkin': bool(self.var_task_checkin.get()),
            'happy': bool(self.var_task_happy.get()),
            'portal': bool(self.var_task_portal.get()),
        }
        errors, warnings = profiles.validate_data(data, tasks)
        if not self._report(errors, warnings):
            return
        name = self.var_name.get().strip()
        if not name:
            messagebox.showerror('保存失败', '用户名称不能为空', parent=self)
            return
        try:
            profiles.write_config(self.slug, data)
            profiles.save_meta(
                self.slug, name=name, enabled=bool(self.var_enabled.get()),
                tasks=tasks)
            profiles.touch_reload()
        except Exception as exc:
            messagebox.showerror('保存失败', str(exc), parent=self)
            return
        if self.on_saved:
            try:
                self.on_saved()
            except Exception:
                pass
        self.destroy()
