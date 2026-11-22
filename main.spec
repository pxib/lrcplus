# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_delvewheel_libs_directory, copy_metadata
from packaging.requirements import Requirement


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

excluded_community_plugins = (
    "anki_flashcards",
    "audio_visualizer",
    "cantonese_readings",
    "english_katakana",
    "lyrics_translator",
    "rhythm_games",
)

datas = [
    (str(PROJECT_ROOT / "LICENSE.txt"), "."),
    (str(PROJECT_ROOT / "assets"), "assets"),
    (str(PROJECT_ROOT / "themedata"), "themedata"),
    (str(PROJECT_ROOT / "docs" / "manual"), "docs/manual"),
    (str(PROJECT_ROOT / "PLUGIN_API.md"), "."),
    (str(PROJECT_ROOT / "core" / "dependency_probe.py"), "core"),
]

plugins_dir = PROJECT_ROOT / "plugins"
excluded_plugin_paths = {("community", name) for name in excluded_community_plugins}
for source in sorted(plugins_dir.rglob("*")):
    relative = source.relative_to(plugins_dir)
    if relative.parts[:2] not in excluded_plugin_paths and source.is_file():
        destination = Path("plugins") / relative.parent
        datas.append((str(source), destination.as_posix()))

datas += collect_data_files("opencc")

updater_exe = PROJECT_ROOT / "dist" / "LRCPlusUpdater.exe"
release_manifest = PROJECT_ROOT / "build" / "lrcplus-update.json"
if not updater_exe.is_file() or not release_manifest.is_file():
    raise FileNotFoundError(
        "Build the standalone updater and release manifest first with tools/build_release.py."
    )
datas.extend([
    (str(updater_exe), "."),
    (str(release_manifest), "."),
])

# Preserve versions so the dependency manager can protect app-provided libraries.
for line in (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
    if line.strip() and not line.lstrip().startswith("#"):
        name = Requirement(line).name
        if name not in {"pytest", "pypresence"}:
            datas += copy_metadata(name, recursive=True)

# aubio-ledfx's Windows wheel carries the continuous Rubber Band R3 engine
# and its native dependencies next to the aubio package.
datas, binaries = collect_delvewheel_libs_directory(
    "aubio", libdir_name="aubio_ledfx.libs", datas=datas,
)


# ---------------------------------------------------------------------------
# PyInstaller analysis
# ---------------------------------------------------------------------------

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],

    pathex=[
        str(PROJECT_ROOT),
    ],

    binaries=binaries,

    datas=datas,

    # These modules are used by plugins loaded from real files.
    hiddenimports=["ui.translation_panel", "PySide6.QtNetwork", "core.rhythm_analysis"],

    hookspath=[str(PROJECT_ROOT / "tools" / "pyinstaller-hooks")],
    hooksconfig=[],
    runtime_hooks=[],

    excludes=[
        # Standalone development tool; run it from the source checkout only.
        "tools.renderer_playground",
        # Omit these plugins even if a bundled module imports them.
        *[f"plugins.community.{name}" for name in excluded_community_plugins],
        # Optional plugin engines are installed through Settings > Dependencies.
        "koroman",
        "alkana",
        "pycantonese",
        "argostranslate",
        "ctranslate2",
        "pypresence",
        "fugashi",
        "unidic_lite",
        # Unused Qt modules are large, especially with PySide6.
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
