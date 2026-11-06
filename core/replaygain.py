"""Read existing ReplayGain tags; never scan or modify audio files."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ReplayGain:
    track_gain: float | None = None
    track_peak: float | None = None
    album_gain: float | None = None
    album_peak: float | None = None

    def multiplier(self, mode="off", preamp=0.0, prevent_clipping=True):
        if mode not in {"track", "album"}:
            return 1.0
        gain, peak = self.track_gain, self.track_peak
        if mode == "album" and self.album_gain is not None:
            gain, peak = self.album_gain, self.album_peak
        if gain is None:
            return 1.0
        result = 10.0 ** (max(-60.0, min(60.0, gain + preamp)) / 20.0)
        if prevent_clipping and peak is not None and peak > 0:
            result = min(result, 1.0 / peak)
        return result


def _number(value):
    if hasattr(value, "text"):
        value = value.text
    if isinstance(value, (tuple, list)):
        value = value[0] if value else ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        result = float(str(value).strip().lower().removesuffix("db").strip())
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def parse_replaygain(tags):
    values = {}
    for key, value in (tags or {}).items():
        key = str(key).lower()
        for prefix in ("txxx:", "----:com.apple.itunes:"):
            if key.startswith(prefix):
                key = key[len(prefix):]
        values[key] = _number(value)
    return ReplayGain(*(values.get("replaygain_" + key) for key in (
        "track_gain", "track_peak", "album_gain", "album_peak",
    )))


def read_replaygain(path):
    from mutagen import File, MutagenError
    try:
        audio = File(path)
        return parse_replaygain(getattr(audio, "tags", None))
    except (OSError, ValueError, TypeError, MutagenError):
        return ReplayGain()
