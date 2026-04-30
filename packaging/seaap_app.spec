# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

project_root = Path.cwd()
entry = str(project_root / "app" / "gui_app.py")

datas = []
proj2 = project_root / "proyecto 2"
if proj2.exists():
    datas.append((str(proj2), "proyecto 2"))
acc = project_root / "app" / "accounts.json"
if acc.exists():
    datas.append((str(acc), "app"))

mp_env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
mp_candidates = []
if mp_env and Path(mp_env).exists():
    mp_candidates.append(Path(mp_env))
else:
    mp_candidates.append(Path.home() / ".cache" / "ms-playwright")
    mp_candidates.append(project_root / "ms-playwright")
for c in mp_candidates:
    if c.exists():
        datas.append((str(c), "ms-playwright"))
        break

hiddenimports = collect_submodules("playwright") + ["tkinter", "PIL", "pystray", "mysql.connector"]
rthooks = [str(project_root / "packaging" / "rthook_playwright.py")]

a = Analysis(
    [entry],
    pathex=[str(project_root), str(project_root / "app")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=rthooks,
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="seaap-app",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
