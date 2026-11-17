"""Collect QtGui support without unused PDF and virtual-keyboard plugins."""

from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies


hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

# Filter before binary dependency analysis so these plugins do not pull in
# QtPdf or the virtual keyboard's QtQuick/QtQml libraries. Keep other image
# formats and native platform input methods for artwork and multilingual text.
excluded_plugins = {"qpdf.dll", "qtvirtualkeyboardplugin.dll"}
binaries = [
    (source, destination)
    for source, destination in binaries
    if Path(source).name.casefold() not in excluded_plugins
]
