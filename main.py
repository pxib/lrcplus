import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from core.plugin_manager import PluginManager
from core.settings import get_disabled_plugins
from ui.main_window import Player


def main() -> int:
    app = QApplication(sys.argv)

    app.setWindowIcon(
        QIcon(str(Path(__file__).resolve().parent / "assets" / "ico.png")
              )
    )

    plugin_manager = PluginManager(disabled_plugins=get_disabled_plugins())
    plugin_manager.discover_and_load(
        Path(__file__).resolve().parent / "plugins"
    )

    window = Player(plugin_manager=plugin_manager)
    plugin_manager.emit("app_ready", window=window)
    window.show()

    exit_code = app.exec()
    plugin_manager.unload_all()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
