import sys
from pathlib import Path

# Worker dispatch must precede GUI and third-party imports in frozen releases.
from core.dependencies import activate_dependencies

if __name__ == "__main__" and "--lrcplus-pinyin-worker" in sys.argv:
    sys.argv.remove("--lrcplus-pinyin-worker")
    from core.pinyin_worker import main as pinyin_worker_main
    raise SystemExit(pinyin_worker_main())

if __name__ == "__main__" and "--lrcplus-dependency-probe" in sys.argv:
    sys.argv.remove("--lrcplus-dependency-probe")
    from core.dependency_probe import main as probe_main
    raise SystemExit(probe_main())

if __name__ == "__main__":
    from core.crash_logging import install_crash_logging
    install_crash_logging()

activate_dependencies()

from core.external_tools import activate_tools

activate_tools()

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from core.plugin_manager import PluginManager
from core.app_info import APP_NAME, APP_VERSION
from core.settings import get_disabled_plugins
from lyrics.furigana import katakana_to_hiragana, token_to_furigana_segments
from ui.input_events import ControlWheelFilter
from ui.main_window import Player

__all__ = [
    "main",
    "katakana_to_hiragana",
    "token_to_furigana_segments",
]


def main() -> int:
    from ui.onboarding import needs_onboarding
    show_onboarding = needs_onboarding()
    update_health_token = None
    for argument in sys.argv[1:]:
        if argument.startswith("--lrcplus-update-health="):
            update_health_token = argument.partition("=")[2]
            sys.argv.remove(argument)
            break

    app = QApplication(sys.argv)
    control_wheel_filter = ControlWheelFilter(app)
    app.installEventFilter(control_wheel_filter)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    app.setWindowIcon(
        QIcon(str(Path(__file__).resolve().parent / "assets" / "ico.png")
              )
    )

    plugin_manager = PluginManager(disabled_plugins=get_disabled_plugins())
    plugin_manager.discover_and_load(
        Path(__file__).resolve().parent / "plugins"
    )

    window = Player(plugin_manager=plugin_manager)
    plugin_manager.set_host_window(window)
    plugin_manager.emit("app_ready", window=window)
    window.show()
    if update_health_token:
        from core.updater import acknowledge_update_health
        QTimer.singleShot(
            8000,
            lambda token=update_health_token: acknowledge_update_health(token),
        )
    from ui.dependency_settings import show_dependency_notice
    def show_startup_dialogs():
        if show_onboarding:
            from ui.onboarding import OnboardingDialog
            OnboardingDialog(window).run()
        show_dependency_notice(plugin_manager, window)

    QTimer.singleShot(0, show_startup_dialogs)

    exit_code = app.exec()
    plugin_manager.unload_all()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
