# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the portable winspace.exe.

Builds a single console-attached exe that doubles as both the GUI
launcher (when invoked with no arguments) and the CLI (when invoked
with a subcommand). The brief console flash on double-click is the
trade-off for a single binary; making the console disappear on
double-click while keeping CLI usable would require a second exe.

Run from the repo root:

    python -m PyInstaller packaging/winspace.spec --noconfirm --clean

Output: ``dist/winspace/winspace.exe`` (one-folder distribution
including PySide6 plugins and runtime).
"""

from pathlib import Path

# pyinstaller injects these globals into the spec namespace
import sys

block_cipher = None

REPO = Path(SPECPATH).parent  # type: ignore[name-defined]  # noqa: F821 - SPECPATH is PyInstaller global

a = Analysis(
    [str(REPO / "src" / "winspace" / "__main__.py")],
    pathex=[str(REPO / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Detectors are discovered via pkgutil.iter_modules at runtime;
        # PyInstaller can miss them, so list them explicitly.
        "winspace.detectors.node_modules",
        "winspace.detectors.browser_cache",
        "winspace.detectors.package_caches",
        "winspace.detectors.cloud_sync",
        "winspace.detectors.im_data",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim PySide6 modules we don't use to keep the bundle small.
        "PySide6.QtNetwork",
        "PySide6.QtSql",
        "PySide6.QtMultimedia",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtTest",
        "PySide6.QtDesigner",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="winspace",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # keep CLI usable; brief console flash on double-click is OK for v1
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=str(REPO / "packaging" / "winspace.ico"),  # add when an icon exists
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="winspace",
)
