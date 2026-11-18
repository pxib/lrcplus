"""Remember editor dimensions without persisting document or tool state."""

from core.debug import debug_print
from core.settings import get_editor_layout_settings, save_editor_layout_settings


class LyricsEditorLayoutMixin:
    def _initialize_editor_layout(self):
        settings = get_editor_layout_settings()
        self._editor_panel_widths = settings["panel_widths"]
        self._editor_layout_ready = False
        size = settings["window_size"]
        if size is None:
            self.resize(1100, 740)
            return
        width, height = size
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            width, height = min(width, available.width()), min(height, available.height())
        self.resize(width, height)

    def showEvent(self, event):
        super().showEvent(event)
        self._restore_editor_panel_widths()

    def _remember_editor_panel_widths(self, *_args):
        if not self._editor_layout_ready or self._closed:
            return
        sizes = self.workspace_splitter.sizes()
        for index, name in ((0, "sidebar"), (2, "karaoke_preview")):
            # Tab-specific hiding is temporary; it must not replace a real
            # width with zero. A user-collapsed splitter panel is still stored.
            if index < len(sizes) and not self.workspace_splitter.widget(index).isHidden():
                self._editor_panel_widths[name] = sizes[index]

    def _restore_editor_panel_widths(self):
        workspace = getattr(self, "workspace_splitter", None)
        if self._closed or workspace is None or not self.isVisible():
            return
        self.layout().activate()
        sizes = [0] * workspace.count()
        visible = sum(not workspace.widget(index).isHidden() for index in range(workspace.count()))
        available = workspace.width() - max(0, visible - 1) * workspace.handleWidth()
        for index, name in ((0, "sidebar"), (2, "karaoke_preview")):
            if index < len(sizes) and not workspace.widget(index).isHidden():
                sizes[index] = self._editor_panel_widths[name]
        sizes[1] = max(1, available - sum(sizes))
        workspace.setSizes(sizes)
        self._editor_layout_ready = True

    def _save_editor_layout(self):
        if self._closed:
            return
        self._remember_editor_panel_widths()
        size = self.normalGeometry().size() if self.isMaximized() or self.isFullScreen() else self.size()
        try:
            save_editor_layout_settings([size.width(), size.height()], self._editor_panel_widths)
        except (OSError, ValueError, TypeError) as error:
            debug_print(f"[Lyrics Editor] Could not save layout: {error!r}")
