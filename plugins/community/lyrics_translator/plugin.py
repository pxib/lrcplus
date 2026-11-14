"""Local and cloud lyric translations in one optional plugin."""
from functools import partial
from PySide6.QtCore import QCoreApplication
from plugins.community.lyrics_translator.cloud_providers import PROVIDERS


class LyricsTranslatorPlugin:
    name = "Lyrics Translator"
    version = "1.3.0"
    description = "Local and cloud lyric translation with editable previews."

    def __init__(self):
        self.dialog = None
        self.window = None
        self.editor_panels = []

    def on_load(self, context):
        self._migrate_settings()
        self.api = context.api
        self.api.on("app_ready", self.on_app_ready)
        self.api.register_translation_provider("argos", "Argos Translate (poor quality)", self.create_editor_panel)
        for provider_id, name in PROVIDERS.items():
            self.api.register_translation_provider(provider_id, name, partial(self.create_cloud_panel, provider_id))
        self.api.add_menu_action("Plugins", "Argos Translate (poor quality)…", self.open_dialog)
        self.application = QCoreApplication.instance()
        if self.application is not None:
            self.application.aboutToQuit.connect(self.shutdown)

    def on_app_ready(self, window):
        self.window = window

    @staticmethod
    def _migrate_settings():
        from core.settings import load_all_settings, save_all_settings
        payload = load_all_settings()
        settings = payload.get("plugin_settings", {})
        if not isinstance(settings, dict):
            return
        current = settings.get("lyrics_translator", {})
        current = dict(current) if isinstance(current, dict) else {}
        migrated = {}
        for old_id in ("argos_translation", "cloud_translation"):
            old = settings.get(old_id, {})
            if isinstance(old, dict):
                migrated.update(old)
        if not migrated:
            return
        migrated.update(current)
        settings["lyrics_translator"] = migrated
        # Keep existing encrypted blobs and contexts intact; no plaintext conversion.
        for old_id in ("argos_translation", "cloud_translation"):
            settings.pop(old_id, None)
        save_all_settings(payload)

    def open_dialog(self):
        from plugins.community.lyrics_translator.dialog import TranslationDialog

        if self.dialog is None:
            self.dialog = TranslationDialog(self.api, self.window)
        else:
            self.dialog.resume()
            self.dialog.load_current()
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def shutdown(self):
        for panel in list(self.editor_panels):
            panel.shutdown()
            panel.setEnabled(False)
        if self.dialog is not None:
            self.dialog.shutdown()
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

    def create_editor_panel(self, editor, document_loader, apply_translations):
        from plugins.community.lyrics_translator.dialog import TranslationPanel

        panel = TranslationPanel(self.api, editor, document_loader=document_loader,
                                 apply_translations=apply_translations)
        self.editor_panels.append(panel)
        panel.destroyed.connect(lambda: self.editor_panels.remove(panel)
                                if panel in self.editor_panels else None)
        return panel

    def create_cloud_panel(self, provider_id, editor, document_loader, apply_translations):
        from plugins.community.lyrics_translator.dialog import TranslationPanel

        panel = TranslationPanel(self.api, editor, cloud_provider=provider_id,
                                 document_loader=document_loader, apply_translations=apply_translations)
        self.editor_panels.append(panel)
        panel.destroyed.connect(lambda: self.editor_panels.remove(panel)
                                if panel in self.editor_panels else None)
        return panel

    def on_unload(self, context):
        self.shutdown()
        if self.application is not None:
            self.application.aboutToQuit.disconnect(self.shutdown)


PLUGIN_CLASS = LyricsTranslatorPlugin
