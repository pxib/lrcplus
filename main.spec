# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


PROJECT_ROOT = Path(SPEC).resolve().parent


# ---------------------------------------------------------------------------
# Runtime files
# ---------------------------------------------------------------------------
#
# LRCPlus discovers plugins dynamically from:
#
#     Path(__file__).resolve().parent / "plugins"
#
# Therefore plugins MUST remain as real files in the distribution rather than
# being treated purely as Python imports.
#
# Same thing for assets/, since main.py accesses:
#
#     assets/ico.png
#
# at runtime.

datas = [
    (str(PROJECT_ROOT / "assets"), "assets"),
    (str(PROJECT_ROOT / "plugins"), "plugins"),
]


# ---------------------------------------------------------------------------
# PyInstaller analysis
# ---------------------------------------------------------------------------

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],

    pathex=[
        str(PROJECT_ROOT),
    ],

    binaries=[],

    datas=datas,

    hiddenimports=[],

    hookspath=[],
    hooksconfig=[],
    runtime_hooks=[],

    # LRCPlus does not need these Qt modules.
    #
    # Keeping them out helps reduce the distribution size considerably,
    # especially with PySide6.
    excludes=[
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtVirtualKeyboard",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DExtras",
        "PySide6.QtBluetooth",
        "PySide6.QtPositioning",
        "PySide6.QtPositioningQuick",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtWebSockets",
    ],

    noarchive=False,
    optimize=0,
)


# ---------------------------------------------------------------------------
# Python module archive
# ---------------------------------------------------------------------------

pyz = PYZ(
    a.pure,
)


# ---------------------------------------------------------------------------
# Executable
# ---------------------------------------------------------------------------

exe = EXE(
    pyz,
    a.scripts,

    # Supporting DLLs/data are placed into the COLLECT directory.
    exclude_binaries=True,

    name="LRCPlus",

    debug=False,
    bootloader_ignore_signals=False,

    strip=False,
    upx=True,

    console=False,

    disable_windowed_traceback=False,
    argv_emulation=False,

    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,

    # Keep the traditional:
    #
    #   LRCPlus/
    #       LRCPlus.exe
    #       assets/
    #       plugins/
    #
    # layout instead of putting PyInstaller's supporting files into
    # a separate _internal directory.
    contents_directory=".",
)


# ---------------------------------------------------------------------------
# One-directory distribution
# ---------------------------------------------------------------------------

coll = COLLECT(
    exe,

    a.binaries,
    a.datas,

    strip=False,
    upx=True,
    upx_exclude=[],

    name="LRCPlus",
)