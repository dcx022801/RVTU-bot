# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).parent
ICON = ROOT / 'win' / 'assets' / 'rvtu.ico'


def collect(path, dest):
    items = []
    for item in path.rglob('*'):
        if item.is_file() and '__pycache__' not in item.parts:
            dest_dir = Path(dest) / item.parent.relative_to(path)
            items.append((str(item), str(dest_dir)))
    return items


datas = (collect(ROOT / 'app', 'app')
         + collect(ROOT / 'defaults', 'defaults')
         + collect(ROOT / 'config', 'config'))

a = Analysis(
    [str(ROOT / 'win' / 'gui.py')],
    pathex=[str(ROOT / 'win'), str(ROOT / 'app'), str(ROOT / 'app' / 'scripts')],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'configutil', 'logutil',
        'argparse', 'base64', 'ctypes', 'email', 'email.message',
        'http.client', 'random', 'secrets', 'smtplib', 'socket', 'ssl',
        'urllib.error', 'urllib.parse', 'urllib.request', 'uuid',
        'msvcrt', 'winreg',
        'pystray', 'pystray._win32',
        'PIL', 'PIL.Image', 'PIL.ImageDraw', 'PIL.ImageTk',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='RVTU-bot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='RVTU-bot',
)
