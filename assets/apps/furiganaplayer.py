import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPainter
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Direct execution starts with assets/apps on sys.path, rather than the repo.
if not __package__:
    project_root = Path(__file__).resolve().parents[2]
    if (project_root / "lyrics" / "furigana.py").is_file() and str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


JOTOBA_URL = "https://jotoba.de/api/search/words"

# Reading generation runs outside the UI thread.
MAX_WORD_LENGTH = 8

JOTOBA_CACHE = {}


def is_kanji(char):
    return "\u3400" <= char <= "\u9fff"


def is_japanese_candidate(char):
    return (
        is_kanji(char)
        or "\u3040" <= char <= "\u309f"
        or "\u30a0" <= char <= "\u30ff"
    )


def is_kana(text):
    return bool(text) and all(
        "\u3040" <= char <= "\u309f"
        or "\u30a0" <= char <= "\u30ff"
        or char == "\u30fc"
        for char in text
    )


def katakana_to_hiragana(text):
    return "".join(
        chr(ord(char) - 0x60)
        if "\u30a1" <= char <= "\u30f6"
        else char
        for char in text
    )


def token_to_furigana_segments(surface, reading):
    """Map a tokenizer's reading onto the kanji portions of one token."""

    reading = katakana_to_hiragana(reading)

    if not reading or reading == "*" or not any(map(is_kanji, surface)):
        return [{"text": surface, "reading": None}]

    segments = []
    text_position = 0
    reading_position = 0
    pattern = re.compile(
        r"([\u3400-\u9fff]+)([\u3040-\u309f\u30a0-\u30ff\u30fc]*)"
    )

    for match in pattern.finditer(surface):
        if match.start() > text_position:
            segments.append({
                "text": surface[text_position:match.start()],
                "reading": None,
            })

        kanji = match.group(1)
        suffix = match.group(2)
        hiragana_suffix = katakana_to_hiragana(suffix)

        if hiragana_suffix:
            suffix_position = reading.find(
                hiragana_suffix,
                reading_position,
            )
            ruby = (
                reading[reading_position:suffix_position]
                if suffix_position >= 0
                else ""
            )
            if suffix_position >= 0:
                reading_position = suffix_position + len(hiragana_suffix)
        else:
            ruby = reading[reading_position:]
            reading_position = len(reading)

        segments.append({"text": kanji, "reading": ruby or None})

        if suffix:
            segments.append({"text": suffix, "reading": None})

        text_position = match.end()

    if text_position < len(surface):
        segments.append({
            "text": surface[text_position:],
            "reading": None,
        })

    return segments


def parse_jotoba_furigana(furigana):
    """
    Convert Jotoba's furigana format:

        [承|うけたまわ]る

    into:

        [
            {"text": "承", "reading": "うけたまわ"},
            {"text": "る", "reading": None},
        ]
    """

    segments = []
    pattern = re.compile(r"\[([^|\]]+)\|([^\]]+)\]")

    position = 0

    for match in pattern.finditer(furigana):
        if match.start() > position:
            segments.append({
                "text": furigana[position:match.start()],
                "reading": None,
            })

        segments.append({
            "text": match.group(1),
            "reading": match.group(2),
        })

        position = match.end()

    if position < len(furigana):
        segments.append({
            "text": furigana[position:],
            "reading": None,
        })

    return segments


def jotoba_lookup(word):
    """
    Search Jotoba for one exact character/word and use the first suggestion.

    For this POC we intentionally do not require secondary == word.
    The user specifically wants the first result as the automatic guess.
    """

    if word in JOTOBA_CACHE:
        return JOTOBA_CACHE[word]

    print(f"[Jotoba] Looking up: {word}")

    try:
        request = Request(
            JOTOBA_URL,
            data=json.dumps({
                "query": word,
                "language": "English",
                "no_english": True,
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            data = json.load(response)

    except (URLError, OSError, ValueError) as error:
        print(f"[Jotoba] FAILED {word!r}: {error}")
        JOTOBA_CACHE[word] = None
        return None

    # The word-search endpoint has used different collection names across
    # versions. ``suggestions`` is used by autocomplete; word searches return
    # their entries as ``results`` on the current endpoint.
    suggestions = next(
        (
            data[key]
            for key in ("results", "words", "suggestions")
            if isinstance(data.get(key), list)
        ),
        [],
    )

    if not suggestions:
        print(
            f"[Jotoba] No results for {word!r}; "
            f"response keys: {list(data) if isinstance(data, dict) else type(data).__name__}"
        )
        JOTOBA_CACHE[word] = None
        return None

    first = suggestions[0]
    reading_data = first.get("reading", {})

    if not isinstance(reading_data, dict):
        print(f"[Jotoba] Invalid reading data for {word!r}: {reading_data!r}")
        JOTOBA_CACHE[word] = None
        return None

    # The word-search response nests all spelling/reading data here:
    # {"reading": {"kana": "うけたまわる", "kanji": "承る",
    #               "furigana": "[承|うけたまわ]る"}}
    furigana = reading_data.get("furigana")
    primary = reading_data.get("kana")
    matched = reading_data.get("kanji")

    print(
        f"[Jotoba] {word!r} -> "
        f"furigana={furigana!r}, primary={primary!r} "
        f"({matched!r})"
    )

    if furigana:
        result = parse_jotoba_furigana(furigana)
    else:
        # Use the kana value only when Jotoba has no detailed ruby markup.
        reading = next(
            (
                value
                for value in (matched, primary)
                if isinstance(value, str) and is_kana(value)
            ),
            None,
        )

        if reading:
            result = [{
                "text": word,
                "reading": reading,
            }]
        else:
            JOTOBA_CACHE[word] = None
            return None

    if not any(segment["reading"] for segment in result):
        print(f"[Jotoba] No ruby annotations for {word!r}")
        JOTOBA_CACHE[word] = None
        return None

    JOTOBA_CACHE[word] = result
    return result


def build_furigana_segments(
    text,
    progress_callback=None,
    should_continue=None,
):
    """
    Reuse the installed app's reading provider without requiring Janome.
    """

    if should_continue and not should_continue():
        return []
    try:
        from lyrics.furigana import build_furigana_segments as generate_readings
    except ImportError:
        # The file can also be copied out of the repo and used with Qt alone.
        segments = [{"text": text, "reading": None}] if text else []
        if progress_callback:
            progress_callback(segments)
        return segments
    return generate_readings(
        text, progress_callback=progress_callback, should_continue=should_continue,
    )


def load_lrc(path):
    lyrics = []

    timestamp_pattern = re.compile(
        r"\[(\d+):(\d+(?:\.\d+)?)\](.*)"
    )

    with open(path, "r", encoding="utf-8-sig") as file:
        for line in file:
            line = line.rstrip("\r\n")

            match = timestamp_pattern.match(line)

            if not match:
                continue

            minutes = int(match.group(1))
            seconds = float(match.group(2))
            text = match.group(3)

            timestamp = minutes * 60 + seconds

            lyrics.append((timestamp, text))

    lyrics.sort(key=lambda lyric: lyric[0])

    return lyrics



class _ReadingBridge(QObject):
    ready = Signal(str, object)


class FuriganaWidget(QWidget):
    BASE_FONT_SIZE = 32
    READING_FONT_SIZE = 18

    PADDING = 6
    READING_BASE_GAP = 2

    def __init__(self, segment_provider=None):
        super().__init__()

        self.segment_provider = segment_provider or build_furigana_segments
        self._executor = None
        self._future = None
        self._closed = False
        self._bridge = _ReadingBridge(self)
        self._bridge.ready.connect(self.furigana_ready)

        self.lyric = ""
        self.segments = []

        self.base_font = QFont()
        self.base_font.setPointSize(self.BASE_FONT_SIZE)

        self.reading_font = QFont()
        self.reading_font.setPointSize(self.READING_FONT_SIZE)

        self.setMinimumHeight(100)

    def set_lyric(self, text):
        self.lyric = text

        if text == getattr(self, "loading_text", None):
            return

        self.segments = [{
            "text": text,
            "reading": None,
        }]

        if not text:
            self.loading_text = None
            self.update()
            return

        self.loading_text = text
        self.update()
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="FuriganaPlayer")
        if self._future is not None:
            self._future.cancel()
        self._future = self._executor.submit(self._generate_readings, text)

    def _generate_readings(self, text):
        if self._closed or text != self.lyric:
            return
        try:
            segments = self.segment_provider(text)
        except Exception as error:
            print(f"Furigana processing failed for {text!r}: {error!r}")
            return
        if not self._closed and text == self.lyric:
            try:
                self._bridge.ready.emit(text, segments)
            except RuntimeError:
                pass  # The widget may have been destroyed while a provider ran.

    def furigana_ready(self, text, segments):
        print(f"[Furigana] Ready: {len(segments)} segments for {text!r}")

        # Ignore an old request if the lyric has already changed.
        if text != self.lyric:
            return

        self.segments = segments
        self.update()

    def stop_workers(self):
        self._closed = True
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def closeEvent(self, event):
        self.stop_workers()
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing
        )

        if not self.lyric:
            return

        base_metrics = QFontMetrics(self.base_font)
        reading_metrics = QFontMetrics(self.reading_font)

        prepared = []
        total_width = 0

        for segment in self.segments:
            base_width = base_metrics.horizontalAdvance(
                segment["text"]
            )

            reading_width = 0

            if segment["reading"]:
                reading_width = reading_metrics.horizontalAdvance(
                    segment["reading"]
                )

            width = max(
                base_width,
                reading_width,
            )

            prepared.append(
                {
                    **segment,
                    "base_width": base_width,
                    "reading_width": reading_width,
                    "width": width,
                }
            )

            total_width += width + self.PADDING

        if prepared:
            total_width -= self.PADDING

        x = (self.width() - total_width) / 2

        base_y = self.height() // 2 + 20
        reading_y = (
            base_y
            - base_metrics.height()
            - self.READING_BASE_GAP
        )

        for segment in prepared:
            width = segment["width"]

            base_x = (
                x
                + (width - segment["base_width"]) / 2
            )

            painter.setFont(self.base_font)

            painter.drawText(
                int(base_x),
                int(base_y),
                segment["text"],
            )

            reading = segment["reading"]

            if reading:
                reading_x = (
                    x
                    + (
                        width
                        - segment["reading_width"]
                    )
                    / 2
                )

                painter.setFont(self.reading_font)

                painter.drawText(
                    int(reading_x),
                    int(reading_y),
                    reading,
                )

                # Long reading connector.
                if (
                    segment["reading_width"]
                    > segment["base_width"] * 1.25
                ):
                    connector_x = (
                        x + width / 2
                    )

                    painter.drawLine(
                        int(connector_x),
                        int(reading_y + 4),
                        int(connector_x),
                        int(
                            base_y
                            - base_metrics.height()
                            + 2
                        ),
                    )

            x += width + self.PADDING


class Player(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Furigana Player")
        self.resize(700, 400)

        self.media_player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.media_player.setAudioOutput(self.audio_output)

        self.lyrics = load_lrc("song.lrc")
        self.current_line = -1
        self.last_lyric_text = None

        self.lyric_widget = FuriganaWidget()

        self.play_button = QPushButton(
            "Play song.mp3"
        )
        self.play_button.clicked.connect(
            self.play_song
        )

        layout = QVBoxLayout()
        layout.addWidget(self.lyric_widget)
        layout.addWidget(self.play_button)

        container = QWidget()
        container.setLayout(layout)

        self.setCentralWidget(container)

        self.timer = QTimer(self)
        self.timer.timeout.connect(
            self.update_lyrics
        )
        self.timer.start(50)

    def play_song(self):
        self.media_player.setSource(QUrl.fromLocalFile(str(Path("song.mp3").resolve())))
        self.media_player.play()

        self.current_line = -1
        self.last_lyric_text = None

    def closeEvent(self, event):
        self.lyric_widget.stop_workers()
        self.timer.stop()
        self.media_player.stop()
        event.accept()

    def update_lyrics(self):
        if self.media_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        position = self.media_player.position() / 1000.0

        for i, (timestamp, text) in enumerate(
            self.lyrics
        ):
            if position >= timestamp:
                self.current_line = i
            else:
                break

        if self.current_line >= 0:
            text = self.lyrics[
                self.current_line
            ][1]

            # Don't hit Jotoba repeatedly while the
            # same lyric line remains active.
            if text != self.last_lyric_text:
                self.last_lyric_text = text
                self.lyric_widget.set_lyric(text)


class EmbeddedPlayer(QWidget):
    """The original two-widget player, borrowing LRCPlus's audio session."""

    def __init__(self, host, return_to_plus):
        super().__init__(host)
        from PySide6.QtGui import QShortcut, QKeySequence

        self.host = host
        self.lyric_widget = FuriganaWidget(self._segments)
        self.play_button = QPushButton()
        self.play_button.clicked.connect(self.play_song)
        self.play_button.setShortcut(QKeySequence("Space"))
        self.return_button = QPushButton("Restore the budget (Return to LRCPlus)")
        self.return_button.clicked.connect(return_to_plus)
        self.return_shortcut = QShortcut(QKeySequence("Escape"), self)
        self.return_shortcut.activated.connect(return_to_plus)

        layout = QVBoxLayout(self)
        layout.addWidget(self.lyric_widget, 1)
        layout.addWidget(self.play_button)
        layout.addWidget(self.return_button)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.sync)
        self.sync()

    def _segments(self, text):
        # Reuse the app's readings without starting a second audio engine.
        source = self.host.lyric_widget
        if source.lyric == text:
            return [dict(segment) for segment in source.segments]
        index = self.host.current_line
        lines = self.host.lyric_lines
        if 0 <= index < len(lines):
            line = lines[index]
            if line.text == text and any(segment.ruby or segment.ruby_spans for segment in line.segments):
                prepared = []
                for segment in line.segments:
                    if segment.ruby_spans:
                        offset = 0
                        for start, end, reading in segment.ruby_spans:
                            if start > offset:
                                prepared.append({"text": segment.text[offset:start], "reading": None})
                            prepared.append({"text": segment.text[start:end], "reading": reading})
                            offset = end
                        if offset < len(segment.text):
                            prepared.append({"text": segment.text[offset:], "reading": None})
                    else:
                        prepared.append({"text": segment.text, "reading": segment.ruby})
                return prepared
        try:
            from lyrics.furigana import get_cached_furigana_segments
        except ImportError:
            cached = None
        else:
            cached = get_cached_furigana_segments(text)
        prepared = cached if cached is not None else [{"text": text, "reading": None}]
        overrides = source.manual_overrides.get(text, {})
        for index, segment in enumerate(prepared):
            if index in overrides:
                segment["reading"] = overrides[index] or None
        return prepared

    def play_song(self):
        if not self.host.current_audio_path:
            self.host.open_audio()
        if self.host.current_audio_path:
            self.host.toggle_playback()
        self.sync()

    def sync(self):
        playing = self.host.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        path = self.host.current_audio_path
        self.play_button.setText("Pause" if playing else f"Play {path.name}" if path else "Open a song")
        index = self.host.current_line
        text = self.host.lyrics[index][1] if 0 <= index < len(self.host.lyrics) else ""
        # Cached readings may arrive after the line itself, even while paused.
        segments = self._segments(text) if text else []
        if text != self.lyric_widget.lyric or segments != self.lyric_widget.segments:
            self.lyric_widget.lyric = text
            self.lyric_widget.segments = segments
            self.lyric_widget.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)


def main():
    app = QApplication(sys.argv)
    window = Player()
    window.show()
    exit_code = app.exec()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
