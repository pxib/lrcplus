"""Extract beat and accent candidates from local audio for rhythm charts."""

from bisect import bisect_left
from dataclasses import dataclass


@dataclass(frozen=True)
class AudioRhythm:
    beats_ms: tuple[int, ...] = ()
    accents_ms: tuple[int, ...] = ()

    @staticmethod
    def _near(events, time_ms, tolerance_ms):
        index = bisect_left(events, time_ms - tolerance_ms)
        return index < len(events) and events[index] <= time_ms + tolerance_ms

    def on_beat(self, time_ms):
        return self._near(self.beats_ms, time_ms, 55)

    def accented(self, time_ms):
        return self._near(self.accents_ms, time_ms, 45)


def analyze_audio_rhythm(path, cancelled):
    if cancelled.is_set():
        return AudioRhythm()

    import aubio
    import numpy as np
    from pedalboard.io import AudioFile

    rate, hop = 22050, 512
    onset = aubio.onset("specflux", hop * 2, hop, rate)
    tempo = aubio.tempo("default", hop * 2, hop, rate)
    beats, onsets = [], []
    with AudioFile(str(path)).resampled_to(rate) as audio:
        while not cancelled.is_set():
            samples = audio.read(hop * 128)
            if samples.shape[1] == 0:
                break
            mono = np.mean(samples, axis=0, dtype=np.float32)
            for start in range(0, len(mono) - hop + 1, hop):
                frame = mono[start:start + hop]
                if tempo(frame)[0] and tempo.get_confidence() >= 0.1:
                    beats.append(round(tempo.get_last_ms()))
                if onset(frame)[0]:
                    strength = float(onset.get_descriptor())
                    if np.isfinite(strength) and strength > 0:
                        onsets.append((round(onset.get_last_ms()), strength))

    if cancelled.is_set():
        return AudioRhythm()
    threshold = np.quantile([strength for _, strength in onsets], 0.75) if onsets else 0
    accents = (time_ms for time_ms, strength in onsets if time_ms >= 0 and strength >= threshold)
    return AudioRhythm(tuple(sorted({time_ms for time_ms in beats if time_ms >= 0})), tuple(sorted(set(accents))))
