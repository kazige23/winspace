# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the portable winspace distribution.

Produces TWO executables sharing one ``_internal/`` runtime:

* ``winspace.exe``      — Windows GUI subsystem (no console window).
                          What end users double-click. Stdout/stderr
                          are detached.
* ``winspace-cli.exe``  — Windows console subsystem. Same entry point,
                          but `winspace-cli.exe scan` (from cmd) shows
                          its output normally.

Both wrap the same Python entry (``winspace.__main__``); the CLI/GUI
dispatch happens at runtime inside ``winspace.cli.main``: no
sub-command -> launch GUI, else -> run that CLI sub-command.

Run from the repo root:

    python -m PyInstaller packaging/winspace.spec --noconfirm --clean

Output: ``dist/winspace/`` with both binaries plus ``_internal/``.
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

_common_exe_kwargs = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon=str(REPO / "packaging" / "winspace.ico"),  # add when an icon exists
)

# GUI launcher — windowed subsystem, no console window on double-click.
exe_gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="winspace",
    console=False,
    **_common_exe_kwargs,
)

# CLI launcher — console subsystem so `winspace-cli.exe scan` from cmd
# shows its output. Same code; just a different exe header.
exe_cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="winspace-cli",
    console=True,
    **_common_exe_kwargs,
)

coll = COLLECT(
    exe_gui,
    exe_cli,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="winspace",
)
