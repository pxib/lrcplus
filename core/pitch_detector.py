"""Microphone pitch tracking for the karaoke pitch lane.

The estimator is a dependency-light implementation of YIN's difference function.
It uses FFT autocorrelation to avoid a compiled pitch-tracking backend while
keeping the public PitchEstimator.estimate(samples) -> (frequency, confidence)
interface used by the karaoke plugin.

Notes:
- `confidence` is a periodicity score, not a probability.
- The estimator expects one mono frame sampled at `sample_rate`.
- Unvoiced/silent frames return (0.0, confidence) rather than a guessed pitch.
"""

from collections import deque
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
        if self.rms_threshold < 0:
            raise ValueError("rms_threshold cannot be negative")
        if not (0 < self.trough_threshold < self.max_cmnd <= 1):
            raise ValueError("Expected 0 < trough_threshold < max_cmnd <= 1")

    def estimate_candidates(self, samples, max_candidates=8):
        """Return plausible (frequency_hz, periodicity_score) candidates.

        The score is derived from the CMNDF trough and is not a probability.
        Multiple candidates preserve alternatives such as octave-related
        periods for the temporal tracker instead of committing too early.
        """
        frame = np.asarray(samples, dtype=np.float64).reshape(-1)
        if frame.size < 8 or not np.all(np.isfinite(frame)):
            return []
        frame = frame - np.mean(frame)
        rms = float(np.sqrt(np.mean(frame * frame)))
        if not np.isfinite(rms) or rms < self.rms_threshold:
            return []

        min_lag = max(2, int(np.floor(self.sample_rate / self.max_frequency)))
        max_lag = min(
            frame.size // 2,
            int(np.ceil(self.sample_rate / self.min_frequency)),
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
        difference[0] = 0.0

        cumulative = np.cumsum(difference[1:])
        cmnd = np.ones(max_lag + 1, dtype=np.float64)
        tau = np.arange(1, max_lag + 1, dtype=np.float64)
        cmnd[1:] = difference[1:] * tau / np.maximum(cumulative, 1e-20)
        cmnd = np.nan_to_num(cmnd, nan=1.0, posinf=1.0, neginf=1.0)

        # Keep all credible local minima, not only the first one. This is a
        # lightweight pYIN-inspired step, not a full probabilistic-YIN model.
        minima = np.flatnonzero(
            (cmnd[min_lag + 1 : max_lag] <= cmnd[min_lag : max_lag - 1])
            & (cmnd[min_lag + 1 : max_lag] < cmnd[min_lag + 2 : max_lag + 1])
        ) + min_lag + 1

        # Include the first threshold-crossing trough even if a flat/noisy
        # shape makes the strict local-minimum test miss it.
        below = np.flatnonzero(cmnd[min_lag : max_lag + 1] < self.trough_threshold)
        if below.size:
            first = min_lag + int(below[0])
            while first < max_lag and cmnd[first + 1] < cmnd[first]:
                first += 1
            minima = np.unique(np.append(minima, first))

        if minima.size == 0:
            best = min_lag + int(np.argmin(cmnd[min_lag : max_lag + 1]))
            if cmnd[best] <= self.max_cmnd:
                minima = np.array([best], dtype=int)

        candidates = []
        for lag in minima:
            lag = int(lag)
            trough = float(cmnd[lag])
            if trough > self.max_cmnd:
                continue

            refined_lag = float(lag)
            if 1 <= lag < max_lag:
                a, b, c = cmnd[lag - 1 : lag + 2]
                denominator = a - 2.0 * b + c
                if abs(denominator) > 1e-12:
                    offset = 0.5 * (a - c) / denominator
                    refined_lag += float(np.clip(offset, -0.5, 0.5))

            if refined_lag <= 0:
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

        if len(unique) > max_candidates:
            # Never discard the first convincing trough when limiting the
            # candidate list; weaker earlier minima may otherwise crowd it out.
            first_convincing = next(
                (
                    i for i, candidate in enumerate(unique)
                    if candidate[1] >= 1.0 - self.trough_threshold
                ),
                None,
            )
            retained = unique[:max_candidates]
            if first_convincing is not None and unique[first_convincing] not in retained:
                retained[-1] = unique[first_convincing]
                retained.sort(key=lambda item: item[0], reverse=True)
            unique = retained
        return unique

    def estimate(self, samples):
        """Return the first convincing YIN trough (frequency, periodicity score)."""
        candidates = self.estimate_candidates(samples)
        if not candidates:
            return 0.0, 0.0
        # Match YIN's first-threshold-crossing rule: the first credible trough
        # in lag order (highest frequency here) wins, not the deepest trough.
        for candidate in candidates:
            if candidate[1] >= 1.0 - self.trough_threshold:
                return candidate
        return max(candidates, key=lambda item: item[1])


class PitchDetector(QThread):
    """Read microphone audio and emit validated, lightly stabilized YIN estimates."""

    pitch_detected = Signal(float, float)
    status_changed = Signal(str)

    SAMPLE_RATE = 44100
    HOP_SIZE = 256
    WINDOW_SIZE = 4096
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

            estimator = PitchEstimator(
                sample_rate,
                min_frequency=self.MIN_FREQUENCY,
                max_frequency=self.MAX_FREQUENCY,
            )
            history = np.zeros(self.WINDOW_SIZE, dtype=np.float32)
            recent_estimates = deque(maxlen=3)
            self.status_changed.emit("Listening...")

            with sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.HOP_SIZE,
            ) as stream:
                while not self._stop_event.is_set():
                    samples, overflowed = stream.read(self.HOP_SIZE)
                    if self._stop_event.is_set():
                        break

                    block = samples[:, 0]
                    if block.size != self.HOP_SIZE:
                        # Defensive: preserve the newest audio if a backend
                        # returns an unexpected block length.
                        if block.size >= self.WINDOW_SIZE:
                            history[:] = block[-self.WINDOW_SIZE :]
                        else:
                            history[:-block.size] = history[block.size:]
                            history[-block.size:] = block
                    else:
                        history[:-self.HOP_SIZE] = history[self.HOP_SIZE:]
                        history[-self.HOP_SIZE:] = block

                    candidates = estimator.estimate_candidates(history)
                    if candidates:
                        # Candidates are ordered by increasing lag (descending
                        # frequency). Prefer the first convincing trough as
                        # YIN does; the deepest trough may be a subharmonic.
                        convincing = next(
                            (
                                candidate
                                for candidate in candidates
                                if candidate[1] >= 1.0 - estimator.trough_threshold
                            ),
                            None,
                        )
                        if convincing is not None:
                            frequency, confidence = convincing
                        else:
                            frequency, confidence = max(
                                candidates, key=lambda item: item[1]
                            )

                        if confidence >= self.MIN_CONFIDENCE:
                            recent_estimates.append((np.log(frequency), confidence))
                            stable_frequency = float(
                                np.exp(np.median([item[0] for item in recent_estimates]))
                            )
                            stable_confidence = float(
                                np.median([item[1] for item in recent_estimates])
                            )
                            self.pitch_detected.emit(stable_frequency, stable_confidence)
                        else:
                            recent_estimates.clear()
                            self.pitch_detected.emit(0.0, confidence)
                    else:
                        # Don't let a previous note's median leak into silence
                        # or an unvoiced consonant.
                        recent_estimates.clear()
                        self.pitch_detected.emit(0.0, 0.0)
        except Exception as exc:
            self.status_changed.emit(f"Microphone unavailable: {exc}")
        finally:
            self._stop_event.clear()
