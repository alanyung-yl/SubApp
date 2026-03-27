# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_data_files


def _spec_root() -> Path:
    # 1) Prefer .spec path passed on CLI
    spec_arg = next((a for a in sys.argv[1:] if a.lower().endswith(".spec")), None)
    if spec_arg:
        return Path(spec_arg).resolve().parent
    # 2) Fallback when __file__ is available
    if "__file__" in globals():
        return Path(__file__).resolve().parent
    # 3) Last fallback
    return Path.cwd()


ROOT = _spec_root()
APP_DIR = ROOT / "SubRename"
APP_SCRIPT = APP_DIR / "SubRenameUI.py"
ICON = APP_DIR / "assets" / "icons" / "appicon.ico"

if not APP_SCRIPT.exists():
    raise FileNotFoundError(f"Missing app entry script: {APP_SCRIPT}")

datas = [
    (str(APP_DIR / "assets"), "assets"),
    (str(APP_DIR / "config" / "langmap.txt"), "config"),
]
datas += collect_data_files("babelfish")

a = Analysis(
    [str(APP_SCRIPT)],
    pathex=[str(APP_DIR), str(ROOT)],  # critical for sibling imports
    binaries=[],
    datas=datas,
    hiddenimports=[
        "app_paths",
        "logging_utils",
        "SubRename",
        "plugins.manager",
        "plugins.context",
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
    exclude_binaries=True,   # onedir pattern
    name="SubApp",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SubApp",
)
