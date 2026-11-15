"""Playback accounting without UI, filesystem, or database work."""

from datetime import datetime, time, timedelta, timezone
import math

from core.sleep_timer import elapsed_seconds


class ListeningStatistics:
    def __init__(self, record, *, enabled=True, clock=elapsed_seconds,
                 wall_clock=lambda: datetime.now().astimezone()):
        self._record = record
        self.enabled = enabled
        self._clock = clock
        self._wall_clock = wall_clock
        self.track = None
        self.duration_ms = 0
        self.attempt_ms = 0
        self.qualified = False
        self._previous = None
        self._pending = {}
        self._last_flush = clock()

    def start_track(self, track, **snapshot):
        self.flush()
        self.track = dict(track) if track else None
        self.duration_ms = 0
        self.attempt_ms = 0
        self.qualified = False
        self.rebase(**snapshot)

    def rebase(self, position_ms=0, *, playing=False, audible=True, rate=1,
               clock_id="native", duration_ms=0):
        self.duration_ms = max(0, duration_ms or self.duration_ms)
        self._previous = (self._clock(), self._wall_clock(), float(position_ms),
                          playing, audible, max(0.01, rate), clock_id)

    def sample(self, position_ms=0, *, playing=False, audible=True, rate=1,
               clock_id="native", duration_ms=0):
        previous = self._previous
        self.rebase(position_ms, playing=playing, audible=audible, rate=rate,
                    clock_id=clock_id, duration_ms=duration_ms)
        now, wall, position, _, _, _, _ = self._previous
        if self.enabled and self.track and previous:
            before, _, old_position, was_playing, was_audible, old_rate, old_clock = previous
            elapsed_ms = max(0, (now - before) * 1000)
            progressed_ms = position - old_position
            # Source jumps are not listening. Explicit seeks also rebase the
            # clock, including small forward seeks that could look like music.
            plausible = progressed_ms <= elapsed_ms * old_rate + 500
            if (was_playing and was_audible and old_clock == clock_id
                    and progressed_ms > 0 and plausible and elapsed_ms > 0):
                listened_ms = min(elapsed_ms, progressed_ms / old_rate)
                if math.isfinite(listened_ms):
                    self._credit(listened_ms, wall)
        if now - self._last_flush >= 15:
            self.flush()

    def _credit(self, milliseconds, end):
        cursor = end - timedelta(milliseconds=milliseconds)
        while cursor < end:
            midnight = datetime.combine(cursor.date() + timedelta(days=1), time.min, cursor.tzinfo)
            boundary = min(end, midnight)
            amount = (boundary - cursor).total_seconds() * 1000
            threshold = min(30000, self.duration_ms / 2) if self.duration_ms > 0 else 30000
            play = int(not self.qualified and self.attempt_ms + amount >= threshold)
            qualifying_time = cursor + timedelta(milliseconds=max(0, threshold - self.attempt_ms)) if play else None
            self.attempt_ms += amount
            self.qualified |= bool(play)
            day = cursor.date().isoformat()
            entry = self._entry(day)
            entry["listened_ms"] += amount
            entry["last_played"] = max(entry["last_played"], boundary.astimezone(timezone.utc).isoformat(timespec="milliseconds"))
            if qualifying_time is not None:
                counted = self._entry(qualifying_time.date().isoformat())
                counted["plays"] += 1
                counted["last_played"] = max(counted["last_played"],
                                             qualifying_time.astimezone(timezone.utc).isoformat(timespec="milliseconds"))
            cursor = boundary

    def _entry(self, day):
        key = (self.track["path_key"], day)
        return self._pending.setdefault(key, {
            **self.track, "day": day, "listened_ms": 0.0, "plays": 0, "last_played": "",
        })

    def flush(self):
        if self._pending:
            records = [{**entry, "listened_ms": round(entry["listened_ms"])}
                       for entry in self._pending.values()
                       if round(entry["listened_ms"]) > 0 or entry["plays"] > 0]
            self._pending.clear()
            if records:
                self._record(records)
        self._last_flush = self._clock()

    def set_enabled(self, enabled, **snapshot):
        self.flush()
        self.enabled = bool(enabled)
        self.attempt_ms = 0
        self.qualified = False
        self.rebase(**snapshot)

    def clear(self, **snapshot):
        self._pending.clear()
        self.attempt_ms = 0
        self.qualified = False
        self.rebase(**snapshot)
