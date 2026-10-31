"""Library search controls and background lyric matching."""

from threading import Event

from PySide6.QtCore import Qt
from PySide6.QtGui import QAbstractTextDocumentLayout, QFont, QPalette, QTextCharFormat, QTextCursor, QTextDocument, QTextOption
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from core.background_job import BackgroundJob
from core.library_lyrics import LibraryLyricsIndex, normalize_lyric_query


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
        self.library_lyrics_status = QLabel()
        self.library_lyrics_status.setWordWrap(True)
        self.library_lyrics_status.hide()
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
        self.library_lyrics_status.hide()

    def _search_library_lyrics(self, query):
        query = normalize_lyric_query(query)
        if query != self._library_lyrics_query:
            self._reset_library_lyrics_search()
            self._library_lyrics_query = query
            self._library_lyrics_cancel = Event()
            self.library_lyrics_status.setText("Searching local lyrics...")
            self.library_lyrics_status.show()
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
        self._library_lyrics_matches = matches
        self.library_lyrics_status.setText(f"{len(matches)} song(s) match the lyrics.")
        self._filter_library_view()

    def _library_lyrics_failed(self, error):
        if (self.library_search_filter.currentData() == "lyrics"
                and normalize_lyric_query(self.library_search.text()) == self._library_lyrics_query):
            self.library_lyrics_status.setText(f"Could not search local lyrics: {error}")

