#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 RVTU-bot Windows 便携版（PyInstaller onedir）并打包 zip。

用法：
    win\\build-exe.bat v2026.10.26
    win\\.venv\\Scripts\\python.exe win\\build_exe.py v2026.10.26
"""
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
BUILD = ROOT / 'build'

GUIDE = """RVTU-bot Windows 便携版

1. 请先解压到可写目录（不要放在 Program Files，程序会在同级目录写入 state\\）。
2. 双击 RVTU-bot.exe 启动。
3. 首次启动会在 state\\config\\ 生成 5 个配置模板；点「打开配置目录」填写。
4. 配置完成后点「重启调度」。配置错误时 GUI 会提示。
5. 关闭窗口 = 最小化到系统托盘；托盘菜单「退出」会停止调度。
6. 开机自启：勾选窗口底部「开机自启」。
7. 日志：state\\logs\\（scheduler.log、driver.log、gui.log 等）。

注意：
- 本程序为个人自动化工具，请遵守学校相关管理规定。
- 移动文件夹后请重新勾选开机自启。
- 未做代码签名，Windows SmartScreen 可能提示，请自行确认来源。
"""


def find_tcl():
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
                return tcl, tk
    return None, None


def write_text(path: Path, text: str, encoding: str = 'utf-8-sig') -> None:
    path.write_text(text.replace('\n', '\r\n'), encoding=encoding)


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else 'v2026.10.26.1'
    tcl, tk = find_tcl()
    env = os.environ.copy()
    if tcl and tk:
        env['TCL_LIBRARY'] = str(tcl)
        env['TK_LIBRARY'] = str(tk)
        print(f'[build] TCL_LIBRARY={tcl}')
        print(f'[build] TK_LIBRARY={tk}')
    else:
        print('[build] 警告：未找到 Tcl/Tk，图形界面可能无法打包')

    if not (ROOT / 'win' / 'assets' / 'rvtu.ico').exists():
        print('[build] 缺少 win/assets/rvtu.ico')
        return 2

    shutil.rmtree(DIST / 'RVTU-bot', ignore_errors=True)
    shutil.rmtree(BUILD / 'RVTU-bot', ignore_errors=True)
    print('[build] 运行 PyInstaller...')
    subprocess.run(
        [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
         str(ROOT / 'win' / 'RVTU-bot.spec')],
        cwd=str(ROOT), env=env, check=True,
    )

    dist = DIST / 'RVTU-bot'
    exe = dist / 'RVTU-bot.exe'
    if not exe.exists():
        print('[build] 未生成 RVTU-bot.exe')
        return 1
    if not (dist / '_internal' / 'app' / 'scheduler.py').is_file():
        print('[build] 未找到打包的 app/scheduler.py')
        return 1

    shutil.copyfile(ROOT / 'README.md', dist / 'README.md')
    write_text(dist / '使用说明.txt', GUIDE)
    write_text(dist / 'VERSION.txt', version + '\n')

    top = f'RVTU-bot-{version}-win64'
    zip_path = DIST / f'{top}.zip'
    if zip_path.exists():
        zip_path.unlink()
    print(f'[build] 打包 {zip_path.name} ...')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for item in sorted(dist.rglob('*')):
            if item.is_file():
                zf.write(item, str(Path(top) / item.relative_to(dist)))

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (zip_path.with_name(zip_path.name + '.sha256')).write_text(
        f'{digest}  {zip_path.name}\n', encoding='utf-8')
    print(f'[build] 完成：{zip_path}')
    print(f'[build] SHA256：{digest}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
