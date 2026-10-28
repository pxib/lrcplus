"""YIN frame estimates, streaming voice tracking, and microphone capture.

Confidence measures periodicity, not the probability that a note is correct.
The estimator and tracker accept mono samples; neither needs an audio device.
"""

import math
import operator
import threading

import numpy as np
from PySide6.QtCore import QThread, Signal


class PitchEstimator:
    """Generate and rank YIN period candidates for one mono audio frame."""

    def __init__(
        self,
        sample_rate,
        min_frequency=55.0,
        max_frequency=1300.0,
        rms_threshold=0.001,
        trough_threshold=0.15,
        max_cmnd=0.35,
    ):
        if not math.isfinite(sample_rate) or sample_rate <= 0:
            raise ValueError("sample_rate must be positive and finite")
        self.sample_rate = int(sample_rate)
        self.min_frequency = float(min_frequency)
        self.max_frequency = float(max_frequency)
        self.rms_threshold = float(rms_threshold)
        self.trough_threshold = float(trough_threshold)
        self.max_cmnd = float(max_cmnd)

        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if not (0 < self.min_frequency < self.max_frequency < self.sample_rate / 2):
            raise ValueError("Expected 0 < min_frequency < max_frequency < Nyquist")
        if not math.isfinite(self.rms_threshold) or self.rms_threshold < 0:
            raise ValueError("rms_threshold must be finite and nonnegative")
        if not (0 < self.trough_threshold < self.max_cmnd <= 1):
            raise ValueError("Expected 0 < trough_threshold < max_cmnd <= 1")

    def estimate_candidates(self, samples, max_candidates=8):
        """Return plausible (frequency_hz, periodicity_score) candidates.

        The score is derived from the CMNDF trough and is not a probability.
        Candidates are ordered by descending frequency. Pass max_candidates=None
        to retain every credible period, including octave-related alternatives.
        """
        if max_candidates is not None:
            max_candidates = operator.index(max_candidates)
            if max_candidates < 1:
                raise ValueError("max_candidates must be positive")
        frame = np.asarray(samples, dtype=np.float64)
        if frame.ndim != 1 or frame.size < 8 or not np.all(np.isfinite(frame)):
            return []
        # Scale before centering/squaring so even large finite inputs cannot
        # overflow the FFT or energy sums. RMS still uses the original scale.
        peak = float(np.max(np.abs(frame)))
        if peak == 0:
            return []
        frame = frame / peak
        frame -= np.mean(frame)
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms <= 1e-12 or rms < self.rms_threshold / peak:
            return []

        min_lag = max(2, int(np.floor(self.sample_rate / self.max_frequency)))
        max_lag = min(
            (frame.size - 1) // 2,
            int(np.ceil(self.sample_rate / self.min_frequency)) + 1,
        )
        if max_lag <= min_lag:
            return []

        # Linear autocorrelation via zero-padded FFT; do not window the input
        # before the YIN difference function.
        fft_size = 1 << (2 * frame.size - 1).bit_length()
        spectrum = np.fft.rfft(frame, n=fft_size)
        autocorrelation = np.fft.irfft(
            spectrum * np.conjugate(spectrum), n=fft_size
        )[: max_lag + 1]

        energy_prefix = np.empty(frame.size + 1, dtype=np.float64)
        energy_prefix[0] = 0.0
        np.cumsum(frame * frame, out=energy_prefix[1:])
        lags = np.arange(max_lag + 1)
        left_energy = energy_prefix[frame.size - lags]
        right_energy = energy_prefix[frame.size] - energy_prefix[lags]
        difference = np.maximum(
            0.0, left_energy + right_energy - 2.0 * autocorrelation
        )
        # Each lag has a different number of overlapping samples. Normalize
        # that count so long periods do not get an artificial score advantage.
        difference /= frame.size - lags
        difference[0] = 0.0

        cumulative = np.cumsum(difference[1:])
        cmnd = np.ones(max_lag + 1, dtype=np.float64)
        tau = np.arange(1, max_lag + 1, dtype=np.float64)
        cmnd[1:] = difference[1:] * tau / np.maximum(cumulative, 1e-20)
        cmnd = np.nan_to_num(cmnd, nan=1.0, posinf=1.0, neginf=1.0)

        # Keep all credible local minima, not only the first one. This is a
        # lightweight pYIN-inspired step, not a full probabilistic-YIN model.
        minima = np.flatnonzero(
            (cmnd[min_lag:max_lag] <= cmnd[min_lag - 1 : max_lag - 1])
            & (cmnd[min_lag:max_lag] < cmnd[min_lag + 1 : max_lag + 1])
        ) + min_lag

        # Guard samples on both sides include true boundary troughs without
        # mistaking a still-descending curve for an in-range fundamental.

        candidates = []
        for lag in minima:
            lag = int(lag)
            refined_lag = float(lag)
            trough = float(cmnd[lag])
            if 1 <= lag < max_lag:
                # Fit the difference itself: CMNDF's changing denominator
                # biases the vertex, especially at high notes / low rates.
                a, b, c = difference[lag - 1 : lag + 2]
                denominator = a - 2.0 * b + c
                if denominator > 1e-12:
                    offset = 0.5 * (a - c) / denominator
                    offset = float(np.clip(offset, -0.5, 0.5))
                    refined_lag += offset
                    fitted_difference = max(0.0, b - 0.25 * (a - c) * offset)
                    trough = fitted_difference * lag / max(cumulative[lag - 1], 1e-20)

            if refined_lag <= 0 or trough > self.max_cmnd:
                continue
            frequency = self.sample_rate / refined_lag
            # Permit a tiny interpolation overshoot at configured boundaries.
            if frequency < self.min_frequency * 0.985:
                continue
            if frequency > self.max_frequency * 1.015:
                continue
            frequency = float(
                np.clip(frequency, self.min_frequency, self.max_frequency)
            )
            candidates.append((frequency, float(np.clip(1.0 - trough, 0.0, 1.0))))

        # De-duplicate adjacent troughs that refine to nearly the same pitch.
        # Preserve lag order: the first convincing trough is important because
        # later troughs can represent integer submultiples of the true F0.
        candidates.sort(key=lambda item: item[0], reverse=True)
        unique = []
        for candidate in candidates:
            if all(
                abs(12.0 * np.log2(candidate[0] / existing[0])) > 0.20
                for existing in unique
            ):
                unique.append(candidate)

        if max_candidates is not None and len(unique) > max_candidates:
            # Retain the selected fundamental even when high-frequency weak
            # troughs would otherwise crowd it out of a limited result list.
            preferred = self.select_candidate(unique)
            retained = sorted(unique, key=lambda item: item[1], reverse=True)[
                :max_candidates
            ]
            if preferred not in retained:
                retained[-1] = preferred
            unique = sorted(retained, key=lambda item: item[0], reverse=True)
        return unique

    def select_candidate(self, candidates):
        """Prefer the first strong period, correcting clearly stronger multiples.

        A weak fundamental with a loud second harmonic can produce a convincing
        half-period trough. Require a substantial score improvement before
        choosing a longer period; equally good repetitions must not halve F0.
        """
        if not candidates:
            return 0.0, 0.0
        ordered = sorted(candidates, key=lambda item: item[0], reverse=True)
        preferred = next(
            (item for item in ordered if item[1] >= 1.0 - self.trough_threshold),
            max(ordered, key=lambda item: item[1]),
        )
        for candidate in ordered:
            ratio = preferred[0] / candidate[0]
            multiple = round(ratio)
            if (2 <= multiple <= 4
                    and abs(12.0 * math.log2(ratio / multiple)) < 0.35
                    and candidate[1] > preferred[1] + 0.04):
                preferred = candidate
        return preferred

    def estimate(self, samples):
        """Return (frequency_hz, periodicity), or (0.0, 0.0) if unvoiced."""
        return self.select_candidate(
            self.estimate_candidates(samples, max_candidates=None)
        )


class PitchTracker:
    """Track mono blocks with short windows and sample-timed note transitions.

    Most voices use 40 ms of audio. Low notes use at least three periods, up to
    80 ms. Silence closes a separate 12 ms gate so old window contents cannot
    sustain a note. Large pitch changes need 12 ms of consistent evidence;
    small changes receive light smoothing in log-frequency space.
    """

    WINDOW_SECONDS = 0.080
    SHORT_WINDOW_SECONDS = 0.040
    GATE_SECONDS = 0.012
    CHANGE_SECONDS = 0.012
    SMOOTH_SECONDS = 0.012

    def __init__(self, sample_rate, min_frequency=55.0, max_frequency=1300.0,
                 rms_threshold=0.001, min_confidence=0.70):
        self.estimator = PitchEstimator(
            sample_rate, min_frequency, max_frequency, rms_threshold
        )
        if not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between zero and one")
        self.sample_rate = self.estimator.sample_rate
        self.min_confidence = float(min_confidence)
        # Keep at least three periods of the configured lowest frequency.
        self.window_size = max(
            round(self.sample_rate * self.WINDOW_SECONDS),
            math.ceil(3 * self.sample_rate / self.estimator.min_frequency) + 2,
        )
        self.short_window_size = max(
            8, round(self.sample_rate * self.SHORT_WINDOW_SECONDS)
        )
        self.gate_size = max(1, round(self.sample_rate * self.GATE_SECONDS))
        self._history = np.zeros(self.window_size, dtype=np.float64)
        self.reset()

    def reset(self):
        """Discard audio and note history after silence or an input gap."""
        self._history.fill(0)
        self._filled = 0
        self._clear_note()

    def _clear_note(self):
        self._log_frequency = None
        self._confidence = 0.0
        self._pending_log = None
        self._pending_samples = 0

    def process(self, samples):
        """Consume a mono block and return the current (frequency, confidence)."""
        block = np.asarray(samples, dtype=np.float64)
        if block.ndim != 1 or not block.size or not np.all(np.isfinite(block)):
            self.reset()
            return 0.0, 0.0
        count = block.size
        if count >= self.window_size:
            self._history[:] = block[-self.window_size:]
        else:
            self._history[:-count] = self._history[count:]
            self._history[-count:] = block
        self._filled = min(self.window_size, self._filled + count)

        if self._filled < self.gate_size:
            return 0.0, 0.0
        recent = self._history[-min(self.gate_size, self._filled):]
        # Audio capture is normalized float32, but use the same scale-safe RMS
        # calculation as the estimator for callers that supply other ranges.
        peak = float(np.max(np.abs(recent)))
        recent_rms = (float(np.std(recent / peak)) if peak else 0.0)
        if (recent_rms <= 1e-12
                or recent_rms < self.estimator.rms_threshold / peak):
            self.reset()
            return 0.0, 0.0
        if self._filled < self.short_window_size:
            return 0.0, 0.0

        frequency, confidence = self.estimator.estimate(
            self._history[-self.short_window_size:]
        )
        if frequency and confidence >= self.min_confidence:
            needed = max(
                self.short_window_size,
                math.ceil(3 * self.sample_rate / frequency) + 2,
            )
            needed = min(needed, self.window_size)
        else:
            needed = self.window_size
        if needed > self.short_window_size:
            if self._filled < needed:
                self._clear_note()
                return 0.0, 0.0
            frequency, confidence = self.estimator.estimate(self._history[-needed:])

        if frequency <= 0 or confidence < self.min_confidence:
            self._clear_note()
            return 0.0, confidence
        log_frequency = math.log2(frequency)
        if self._log_frequency is None:
            self._log_frequency = log_frequency
        elif abs(12 * (log_frequency - self._log_frequency)) > 0.75:
            if (self._pending_log is None
                    or abs(12 * (log_frequency - self._pending_log)) > 0.5):
                self._pending_samples = 0
            self._pending_log = log_frequency
            self._pending_samples += count
            if self._pending_samples < self.sample_rate * self.CHANGE_SECONDS:
                return 2.0 ** self._log_frequency, min(confidence, self._confidence)
            # Commit a confirmed interval directly; averaging two different
            # notes would draw pitches that were never sung.
            self._log_frequency = log_frequency
            self._pending_log = None
            self._pending_samples = 0
        else:
            alpha = -math.expm1(-count / (self.sample_rate * self.SMOOTH_SECONDS))
            self._log_frequency += alpha * (log_frequency - self._log_frequency)
            self._pending_log = None
            self._pending_samples = 0
        self._confidence = confidence
        return float(2.0 ** self._log_frequency), confidence


class PitchDetector(QThread):
    """Read microphone audio and emit streaming voice estimates."""

    pitch_detected = Signal(float, float)
    status_changed = Signal(str)

    SAMPLE_RATE = 44100
    HOP_SIZE = 256
    WINDOW_SIZE = round(SAMPLE_RATE * PitchTracker.WINDOW_SECONDS)
    MIN_FREQUENCY = 55.0
    MAX_FREQUENCY = 1300.0
    MIN_CONFIDENCE = 0.70

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()

    def stop(self):
        """Request shutdown; the stream loop exits after its next read."""
        self._stop_event.set()

    def run(self):
        if self._stop_event.is_set():
            return
        try:
            import sounddevice as sd
        except ImportError:
            self.status_changed.emit(
                "Pitch detection needs sounddevice; install project requirements."
            )
            return

        try:
            sample_rate = self.SAMPLE_RATE
            try:
                sd.check_input_settings(
                    samplerate=sample_rate, channels=1, dtype="float32"
                )
            except Exception:
                device = sd.query_devices(kind="input")
                sample_rate = int(round(device["default_samplerate"]))
                sd.check_input_settings(
                    samplerate=sample_rate, channels=1, dtype="float32"
                )

            tracker = PitchTracker(
                sample_rate,
                min_frequency=self.MIN_FREQUENCY,
                max_frequency=self.MAX_FREQUENCY,
                min_confidence=self.MIN_CONFIDENCE,
            )
            hop_size = max(1, round(self.HOP_SIZE * sample_rate / self.SAMPLE_RATE))
            if self._stop_event.is_set():
                return

            with sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=hop_size,
                latency="low",
            ) as stream:
                self.status_changed.emit("Listening...")
                while not self._stop_event.is_set():
                    samples, overflowed = stream.read(hop_size)
                    if self._stop_event.is_set():
                        break

                    if overflowed:
                        # Missing samples break periodicity and temporal
                        # continuity. Start fresh with the newly received block.
                        tracker.reset()
                    frequency, confidence = tracker.process(samples[:, 0])
                    self.pitch_detected.emit(frequency, confidence)
        except Exception as exc:
            self.status_changed.emit(f"Microphone unavailable: {exc}")
