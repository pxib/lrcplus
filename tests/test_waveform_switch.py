"""Track changes must not join a decoder on the GUI thread."""

import threading
from io import BytesIO

from PySide6.QtCore import QThread, QTimer, Signal

from lyrics import waveform


def test_peak_calculation_stops_after_cancellation():
    checks = []

    def cancelled():
        checks.append(True)
        return len(checks) > 3

    assert waveform._waveform_peaks_from_pcm(BytesIO(bytes(8000)), 200, cancelled) == ([], 0)
    assert len(checks) == 4


def test_switching_tracks_does_not_wait_for_cancelled_decoder(qapp, monkeypatch):
    decoders = []

    class Decoder(QThread):
        loaded = Signal(list, int)
        failed = Signal(str)

        def __init__(self, path, parent=None):
            super().__init__(parent)
            self.path = path
            self.entered = threading.Event()
            self.release = threading.Event()
            self.cancelled = False
            self.wait_calls = 0
            decoders.append(self)

        def run(self):
            self.entered.set()
            self.release.wait(5)
            # Deliberately simulate a result already in flight at cancellation.
            self.loaded.emit([float(self.path)], int(self.path) * 1000)
            if self.cancelled:
                self.failed.emit("old decoder failed")

        def cancel(self):
            self.cancelled = True

        def wait(self, *args):
            self.wait_calls += 1
            return super().wait(*args)

    monkeypatch.setattr(waveform, "_WaveformWorker", Decoder)
    monkeypatch.setattr(waveform, "find_ffmpeg", lambda: "ffmpeg")
    widget = waveform.WaveformWidget()
    try:
        for path in ("1", "2", "3"):
            widget.load_audio(path)
            assert decoders[-1].entered.wait(2)
        assert all(worker.wait_calls == 0 for worker in decoders)
        assert all(worker.cancelled for worker in decoders[:-1])
        assert widget._retired_workers == set(decoders[:-1])
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        qapp.processEvents()
        assert heartbeat

        # The newest song's result wins, even when old jobs finish afterward.
        decoders[-1].release.set()
        QThread.wait(decoders[-1], 2000)
        qapp.processEvents()
        assert widget._peaks == [3.0]
        for worker in decoders[:-1]:
            worker.release.set()
            QThread.wait(worker, 2000)
        qapp.processEvents()
        assert widget._peaks == [3.0]
        assert widget._duration_ms == 3000
        assert widget._error is None
        assert not widget._retired_workers
    finally:
        for worker in decoders:
            worker.release.set()
        widget._stop_worker()
        widget.deleteLater()
        qapp.processEvents()
