"""Library search controls and background lyric matching."""

from pathlib import Path
from threading import Event

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QFont, QMovie, QPalette, QPixmap,
    QTextCharFormat, QTextCursor, QTextDocument, QTextOption,
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QWidget,
)

from core.background_job import BackgroundJob
from core.library_lyrics import LibraryLyricsIndex, normalize_lyric_query


APP_ROOT = Path(__file__).resolve().parents[1]


class LyricMatchDelegate(QStyledItemDelegate):
    """Draw normal library cells with only the first lyric match in bold."""

    @staticmethod
    def _document(text, span, font):
        document = QTextDocument()
        document.setDefaultFont(font)
        document.setDocumentMargin(0)
        option = document.defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.NoWrap)
        document.setDefaultTextOption(option)
        cursor = QTextCursor(document)
        start, end = span
        cursor.insertText(text[:start])
        bold = QTextCharFormat()
        bold.setFontWeight(QFont.Weight.Bold)
        cursor.insertText(text[start:end], bold)
        cursor.insertText(text[end:], QTextCharFormat())
        return document

    def paint(self, painter, option, index):
        span = index.data(Qt.ItemDataRole.UserRole)
        if not span:
            return super().paint(painter, option, index)
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        style = styled.widget.style() if styled.widget else QApplication.style()
        text_rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, styled, styled.widget)
        document = self._document(styled.text, span, styled.font)
        styled.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette = styled.palette
        if styled.state & QStyle.StateFlag.State_Selected:
            context.palette.setColor(QPalette.ColorRole.Text, styled.palette.color(QPalette.ColorRole.HighlightedText))
        painter.save()
        painter.setClipRect(text_rect)
        painter.translate(text_rect.left(), text_rect.top() + (text_rect.height() - document.size().height()) / 2)
        document.documentLayout().draw(painter, context)
        painter.restore()


class LibraryLyricsSearchMixin:
    def _build_library_search_filter(self, toolbar):
        self.library_search_filter = QComboBox()
        self.library_search_filter.setAccessibleName("Library search filter")
        self.library_search_filter.addItem("Track details", "metadata")
        self.library_search_filter.addItem("Lyrics", "lyrics")
        self.library_search_filter.setToolTip("Choose whether to search track details or local lyric text")
        toolbar.addWidget(self.library_search_filter)
        self.library_lyrics_status_widget = QWidget()
        status_layout = QHBoxLayout(self.library_lyrics_status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(6)
        self.library_lyrics_loading = QLabel()
        self.library_lyrics_loading.setAccessibleName("Searching lyrics")
        self.library_lyrics_loading.setFixedSize(24, 24)
        self.library_lyrics_movie = QMovie(
            str(APP_ROOT / "assets" / "media" / "loading.gif"),
            parent=self.library_lyrics_loading,
        )
        self.library_lyrics_movie.setScaledSize(QSize(24, 24))
        self.library_lyrics_loading.setMovie(self.library_lyrics_movie)
        self.library_lyrics_result = QLabel()
        self.library_lyrics_result.setFixedSize(24, 24)
        self.library_lyrics_result.hide()
        self.library_lyrics_status = QLabel()
        self.library_lyrics_status.setWordWrap(True)
        self.library_lyrics_status.hide()
        status_layout.addWidget(self.library_lyrics_loading)
        status_layout.addWidget(self.library_lyrics_result)
        status_layout.addWidget(self.library_lyrics_status, 1)
        self.library_lyrics_status_widget.hide()
        self._library_lyrics_index = LibraryLyricsIndex()
        self._library_lyrics_job = BackgroundJob(self, "library-lyrics")
        self._library_lyrics_cancel = Event()
        self._library_lyrics_query = None
        self._library_lyrics_matches = {}
        self._library_lyrics_job.finished.connect(self._library_lyrics_finished)
        self._library_lyrics_job.failed.connect(self._library_lyrics_failed)
        self.library_search_filter.currentIndexChanged.connect(self._library_search_filter_changed)

    def _library_search_filter_changed(self):
        lyrics = self.library_search_filter.currentData() == "lyrics"
        self.library_search.setPlaceholderText(
            "Search words in local lyrics..." if lyrics else "Search library..."
        )
        self.library_table.setColumnHidden(self.library_lyrics_column, not lyrics)
        self._reset_library_lyrics_search()
        self._filter_library_view()

    def _reset_library_lyrics_search(self):
        self._library_lyrics_cancel.set()
        self._library_lyrics_job.cancel()
        self._library_lyrics_query = None
        self._library_lyrics_matches = {}
        self.library_lyrics_movie.stop()
        self.library_lyrics_loading.hide()
        self.library_lyrics_result.hide()
        self.library_lyrics_status.hide()
        self.library_lyrics_status_widget.hide()

    def _search_library_lyrics(self, query):
        query = normalize_lyric_query(query)
        if query != self._library_lyrics_query:
            self._reset_library_lyrics_search()
            self._library_lyrics_query = query
            self._library_lyrics_cancel = Event()
            self.library_lyrics_status.setText("Searching local lyrics...")
            self.library_lyrics_status.show()
            self.library_lyrics_result.hide()
            self.library_lyrics_loading.show()
            self.library_lyrics_status_widget.show()
            self.library_lyrics_movie.start()
            current = None
            if self.current_audio_path and self.current_lyrics_path:
                current = (str(self.current_audio_path), str(self.current_lyrics_path))
            self._library_lyrics_job.start(
                self._library_lyrics_index.search,
                [str(track.path) for track in self.library_tracks],
                query, self._library_lyrics_cancel, current,
            )
        return self._library_lyrics_matches

    def _library_lyrics_finished(self, payload):
        query, matches = payload
        if (self.library_search_filter.currentData() != "lyrics"
                or normalize_lyric_query(self.library_search.text()) != query):
            return
        self.library_lyrics_movie.stop()
        self.library_lyrics_loading.hide()
        icon = "success.png" if matches else "fail.png"
        self.library_lyrics_result.setPixmap(
            QPixmap(str(APP_ROOT / "assets" / "media" / icon)).scaled(
                QSize(24, 24), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.library_lyrics_result.setAccessibleName(
            "Lyrics found" if matches else "No lyrics found"
        )
        self.library_lyrics_result.show()
        self._library_lyrics_matches = matches
        self.library_lyrics_status.setText(f"{len(matches)} song(s) match the lyrics.")
        self._filter_library_view()

    def _library_lyrics_failed(self, error):
        if (self.library_search_filter.currentData() == "lyrics"
                and normalize_lyric_query(self.library_search.text()) == self._library_lyrics_query):
            self.library_lyrics_movie.stop()
            self.library_lyrics_loading.hide()
            self.library_lyrics_result.setPixmap(
                QPixmap(str(APP_ROOT / "assets" / "media" / "error.png")).scaled(
                    QSize(24, 24), Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.library_lyrics_result.setAccessibleName("Lyrics search error")
            self.library_lyrics_result.show()
            self.library_lyrics_status.setText(f"Could not search local lyrics: {error}")
