"""Microphone pitch tracking for the karaoke pitch lane.

The estimator uses a YIN-style difference function calculated with FFT
autocorrelation. Keeping the estimator here avoids requiring a compiled pitch
tracking backend and gives us control over the frequency and confidence gates.
"""

import numpy as np
from collections import deque
from PySide6.QtCore import QThread, Signal


class PitchEstimator:
    """Estimate the fundamental frequency of one mono audio frame."""

    def __init__(self, sample_rate, min_frequency=55.0, max_frequency=1000.0):
        self.sample_rate = int(sample_rate)
        self.min_frequency = float(min_frequency)
        self.max_frequency = float(max_frequency)

    def estimate(self, samples):
        frame = np.asarray(samples, dtype=np.float64).reshape(-1)
        if frame.size < 4 or not np.all(np.isfinite(frame)):
            return 0.0, 0.0

        frame = frame - np.mean(frame)
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms < 0.001:
            return 0.0, 0.0

        # A small taper reduces edge discontinuities without shortening the
        # usable lag range needed for lower singing voices.
        frame *= np.hanning(frame.size)
        fft_size = 1 << (2 * frame.size - 1).bit_length()
        spectrum = np.fft.rfft(frame, n=fft_size)
        autocorrelation = np.fft.irfft(spectrum * np.conjugate(spectrum), n=fft_size)
        autocorrelation = autocorrelation[:frame.size]

        # YIN's squared-difference function from autocorrelation and prefix
        # energies, evaluated only over the lag range we can use.
        min_lag = max(2, int(self.sample_rate / self.max_frequency))
        max_lag = min(frame.size // 2, int(self.sample_rate / self.min_frequency))
        if max_lag <= min_lag:
            return 0.0, 0.0
        energy_prefix = np.concatenate(([0.0], np.cumsum(frame * frame)))
        lags = np.arange(max_lag + 1)
        left_energy = energy_prefix[frame.size - lags]
        right_energy = energy_prefix[frame.size] - energy_prefix[lags]
        difference = np.maximum(0.0, left_energy + right_energy - 2.0 * autocorrelation[:max_lag + 1])

        cumulative = np.cumsum(difference[1:])
        cmnd = np.ones(max_lag + 1, dtype=np.float64)
        tau = np.arange(1, max_lag + 1)
        cmnd[1:] = difference[1:] * tau / np.maximum(cumulative, 1e-12)

        # Select the first convincing valley, then follow it to its local
        # minimum. This favors the fundamental over a stronger second harmonic.
        candidates = np.flatnonzero(cmnd[min_lag:max_lag] < 0.15) + min_lag
        if candidates.size:
            best_lag = int(candidates[0])
            while best_lag + 1 <= max_lag and cmnd[best_lag + 1] < cmnd[best_lag]:
                best_lag += 1
        else:
            best_lag = int(np.argmin(cmnd[min_lag:max_lag + 1]) + min_lag)
            if cmnd[best_lag] > 0.35:
                return 0.0, max(0.0, min(1.0, 1.0 - float(cmnd[best_lag])))

        # Parabolic interpolation removes most of the one-sample quantization.
        if 1 <= best_lag < max_lag:
            a, b, c = cmnd[best_lag - 1:best_lag + 2]
            denominator = a - 2.0 * b + c
            if abs(denominator) > 1e-12:
                best_lag += float(0.5 * (a - c) / denominator)

        frequency = self.sample_rate / best_lag
        if not self.min_frequency <= frequency <= self.max_frequency:
            return 0.0, 0.0
        confidence = max(0.0, min(1.0, 1.0 - float(cmnd[int(round(best_lag))])))
        return float(frequency), confidence


class PitchDetector(QThread):
    """Read the default microphone and emit robust YIN pitch estimates."""

    pitch_detected = Signal(float, float)
    status_changed = Signal(str)

    SAMPLE_RATE = 44100
    HOP_SIZE = 256
    WINDOW_SIZE = 4096

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            import sounddevice as sd
        except ImportError:
            self.status_changed.emit("Pitch detection needs sounddevice; install project requirements.")
            return

        try:
            sample_rate = self.SAMPLE_RATE
            try:
                sd.check_input_settings(samplerate=sample_rate, channels=1, dtype="float32")
            except Exception:
                device = sd.query_devices(kind="input")
                sample_rate = int(round(device["default_samplerate"]))

            estimator = PitchEstimator(sample_rate)
            history = np.zeros(self.WINDOW_SIZE, dtype=np.float32)
            recent_estimates = deque(maxlen=3)
            self.status_changed.emit("Listening...")
            with sd.InputStream(
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.HOP_SIZE,
            ) as stream:
                while not self._stop_requested:
                    samples, _overflowed = stream.read(self.HOP_SIZE)
                    if self._stop_requested:
                        break
                    block = samples[:, 0]
                    history[:-self.HOP_SIZE] = history[self.HOP_SIZE:]
                    history[-self.HOP_SIZE:] = block
                    frequency, confidence = estimator.estimate(history)
                    if frequency > 0 and confidence >= 0.70:
                        # Median in log-frequency space suppresses one-frame
                        # octave glitches while keeping the tracker responsive
                        # to actual note changes (about 12 ms at this hop size).
                        recent_estimates.append((np.log(frequency), confidence))
                        stable_frequency = float(np.exp(np.median(
                            [estimate[0] for estimate in recent_estimates]
                        )))
                        stable_confidence = float(np.median(
                            [estimate[1] for estimate in recent_estimates]
                        ))
                        self.pitch_detected.emit(stable_frequency, stable_confidence)
                    else:
                        recent_estimates.clear()
                        self.pitch_detected.emit(0.0, confidence)
        except Exception as exc:
            self.status_changed.emit(f"Microphone unavailable: {exc}")
