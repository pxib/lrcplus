"""Read-only playback details for the Advanced settings page."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel

from core.background_job import BackgroundJob


def _read_source_details(path):
    from mutagen import File, MutagenError

    try:
        info = getattr(File(path), "info", None)
    except (OSError, ValueError, TypeError, MutagenError):
        return "", 0

    codec = getattr(info, "codec_description", "")
    if not codec:
        format_name = type(info).__module__.removeprefix("mutagen.").partition(".")[0]
        codec = {
            "aac": "AAC", "flac": "FLAC", "oggflac": "FLAC",
            "oggopus": "Opus", "oggvorbis": "Vorbis", "oggspeex": "Speex",
            "mp3": "MP3" if getattr(info, "layer", None) == 3 else "",
            "wave": {1: "PCM", 3: "Floating-point PCM"}.get(getattr(info, "audio_format", None), ""),
        }.get(format_name, "")
    return codec, getattr(info, "sample_rate", 0)


class AudioSignalPathPanel(QGroupBox):
    def __init__(self, snapshot, parent=None):
        super().__init__("Audio signal path", parent)
        self.setProperty("settingsLabel", self.title())
        self.setProperty("settingsKeywords", "codec device sample rate output ReplayGain DSP gapless crossfade diagnostics")
        self._snapshot = snapshot
        self._path = ""
        self._source_details = ("", 0)
        self._values = {}
        form = QFormLayout(self)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for name in ("Codec", "Source sample rate", "Playback route", "Audio output",
                     "Output sample rate", "ReplayGain", "DSP", "Gapless", "Crossfade"):
            value = QLabel("Unavailable")
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            form.addRow(name, value)
            self._values[name] = value
        self._job = BackgroundJob(self, "signal-path-metadata")
        self._job.finished.connect(self._apply_source_details)
        self.destroyed.connect(self._job.close)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        if self._snapshot is not None:
            self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _apply_source_details(self, details):
        self._source_details = details
        if self.isVisible():
            self.refresh()

    def refresh(self):
        if self._snapshot is None:
            return
        source, values = self._snapshot()
        values = dict(values)
        path = source.toLocalFile()
        if path != self._path:
            self._path = path
            self._source_details = ("", 0)
            self._job.cancel()
            if path:
                self._job.start(_read_source_details, path)
        codec, sample_rate = self._source_details
        values["Codec"] = values["Codec"] or codec
        values["Source sample rate"] = f"{sample_rate:,} Hz" if sample_rate else ""
        for name, label in self._values.items():
            text = values.get(name) or "Unavailable"
            if label.text() != text:
                label.setText(text)
