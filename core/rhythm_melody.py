"""Find stable pitched notes, including quieter notes under louder sounds."""

from dataclasses import dataclass
from math import log2

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


WINDOW_SIZE = 2048
FFT_SIZE = 4096
FIRST_MIDI_NOTE = 48  # C3
LAST_MIDI_NOTE = 91  # G6
MIN_NOTE_MS = 110


@dataclass(frozen=True)
class MelodyNote:
    start_ms: int
    end_ms: int
    midi_note: int
    strength: float = 0.0


class MelodyAnalyzer:
    """Scan overlapping windows and keep evidence for several pitches per frame."""

    def __init__(self, rate, hop):
        self.rate = rate
        self.hop = hop
        self.frame_ms = hop * 1000 / rate
        self._bin_hz = rate / FFT_SIZE
        self._low_bin = round(55 / self._bin_hz)
        self._high_bin = round(1700 / self._bin_hz)
        self._window = np.hanning(WINDOW_SIZE).astype(np.float32)
        self._window *= 2 / self._window.sum()
        self._pending = None
        self._pitch_frames = []
        self._sample_count = 0

    def feed(self, mono, side=None):
        self._sample_count += len(mono)
        signals = mono[np.newaxis] if side is None else np.stack((mono, side))
        if self._pending is None:
            self._pending = np.zeros((len(signals), WINDOW_SIZE // 2), dtype=np.float32)
        self._scan(signals)

    def _scan(self, samples):
        data = np.concatenate((self._pending, samples), axis=-1)
        if data.shape[-1] < WINDOW_SIZE:
            self._pending = data
            return
        frames = sliding_window_view(data, WINDOW_SIZE, axis=-1)[:, ::self.hop]
        count = frames.shape[1]
        self._pending = data[:, count * self.hop:].copy()
        pitches = np.zeros((count, LAST_MIDI_NOTE - FIRST_MIDI_NOTE + 1), dtype=np.float32)
        for signal in frames:
            spectra = np.abs(np.fft.rfft(signal * self._window, n=FFT_SIZE, axis=1)[:, :self._high_bin + 2])
            self._collect_peaks(spectra, pitches)
        self._pitch_frames.append(pitches)

    def _collect_peaks(self, spectra, pitches):
        band = spectra[:, self._low_bin:self._high_bin + 1]
        # A relative floor keeps quieter pitches while rejecting the window's sidelobes.
        floor = np.maximum(0.0015, 0.055 * band.max(axis=1, keepdims=True))
        peaks = ((band >= floor) & (band > spectra[:, self._low_bin - 1:self._high_bin])
                 & (band >= spectra[:, self._low_bin + 1:self._high_bin + 2]))
        for index, (spectrum, peak_mask) in enumerate(zip(spectra, peaks)):
            fundamentals = []
            for bin_index in np.flatnonzero(peak_mask) + self._low_bin:
                left, peak, right = np.log(np.maximum(spectrum[bin_index - 1:bin_index + 2], 1e-12))
                curvature = float(left - 2 * peak + right)
                offset = 0.5 * float(left - right) / curvature if curvature < 0 else 0
                frequency = (bin_index + offset) * self._bin_hz
                magnitude = spectrum[bin_index]
                if any(_is_harmonic(frequency, magnitude, base, strength) for base, strength in fundamentals):
                    continue
                fundamentals.append((frequency, magnitude))
                midi = round(69 + 12 * log2(frequency / 440))
                if FIRST_MIDI_NOTE <= midi <= LAST_MIDI_NOTE:
                    column = midi - FIRST_MIDI_NOTE
                    pitches[index, column] = max(pitches[index, column], magnitude)

    def finish(self, cancelled):
        """Return note spans after rejecting brief peaks and allowing one missed frame."""
        if self._pending is None or cancelled.is_set():
            return ()
        self._scan(np.zeros((len(self._pending), WINDOW_SIZE // 2), dtype=np.float32))
        if not self._pitch_frames:
            return ()
        pitches = np.concatenate(self._pitch_frames).T
        self._pitch_frames.clear()
        track_ms = round(self._sample_count * 1000 / self.rate)
        notes = []
        for midi, strengths in enumerate(pitches, FIRST_MIDI_NOTE):
            if cancelled.is_set():
                return ()
            present = strengths > 0
            joined = present.copy()
            joined[1:-1] |= present[:-2] & present[2:]
            edges = np.flatnonzero(np.diff(np.pad(joined, (1, 1))))
            for start, end in zip(edges[::2], edges[1::2]):
                segment = strengths[start:end]
                if np.count_nonzero(segment) * self.frame_ms < MIN_NOTE_MS:
                    continue
                audible = np.flatnonzero(segment >= segment.max() * 0.45)
                note_start = max(0, round((start + audible[0] - 0.5) * self.frame_ms))
                note_end = min(track_ms, round((start + audible[-1] + 0.5) * self.frame_ms))
                if note_end - note_start >= MIN_NOTE_MS:
                    strength = float(np.median(segment[audible]))
                    notes.append(MelodyNote(note_start, note_end, midi, strength))
        return tuple(sorted(notes, key=lambda note: (note.start_ms, note.midi_note)))


def _is_harmonic(frequency, magnitude, base, strength):
    multiple = round(frequency / base)
    return 2 <= multiple <= 6 and abs(frequency - multiple * base) <= max(2.5, frequency * 0.012) and magnitude <= strength
