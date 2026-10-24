"""Microphone pitch tracking for the karaoke pitch prototype."""

from PySide6.QtCore import QThread, Signal


class PitchDetector(QThread):
    """Read the default microphone and emit aubio pitch estimates."""

    pitch_detected = Signal(float, float)
    status_changed = Signal(str)

    SAMPLE_RATE = 44100
    HOP_SIZE = 512
    WINDOW_SIZE = 2048

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            import aubio
            import sounddevice as sd
        except ImportError as exc:
            self.status_changed.emit(
                f"Pitch detection needs {exc.name}; install project requirements."
            )
            return

        try:
            tracker = aubio.pitch(
                "yin", self.WINDOW_SIZE, self.HOP_SIZE, self.SAMPLE_RATE
            )
            tracker.set_unit("Hz")
            tracker.set_tolerance(0.8)
            # A quieter gate helps laptop and headset microphones. Aubio's
            # silence value is a dB threshold, so lower values are more
            # sensitive.
            tracker.set_silence(-60)
            self.status_changed.emit("Listening...")

            with sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=self.HOP_SIZE,
            ) as stream:
                while not self._stop_requested:
                    samples, overflowed = stream.read(self.HOP_SIZE)
                    if self._stop_requested:
                        break
                    frequency = float(tracker(samples[:, 0])[0])
                    confidence = float(tracker.get_confidence())
                    # Confidence varies with mic placement and vocal timbre;
                    # keep plausible estimates and let the UI show their score.
                    if frequency > 0 and confidence >= 0.30:
                        self.pitch_detected.emit(frequency, confidence)
                    else:
                        self.pitch_detected.emit(0.0, confidence)
        except Exception as exc:
            self.status_changed.emit(f"Microphone unavailable: {exc}")

