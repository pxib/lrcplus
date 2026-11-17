"""Small, bounded shuffle preferences; every eligible track stays playable."""

from datetime import datetime, timezone


def track_weight(*, favorite=False, last_played=None, prefer_favorites=True,
                 prefer_older=True, now=None):
    weight = 2.0 if favorite and prefer_favorites else 1.0
    if prefer_older and last_played:
        try:
            played = datetime.fromisoformat(last_played)
            if played.tzinfo is None:
                played = played.replace(tzinfo=timezone.utc)
            age = ((now or datetime.now(timezone.utc)) - played).total_seconds() / 86400
            # Recover from 20% to full weight over two weeks. Unheard tracks
            # retain full weight; future timestamps receive the recent penalty.
            weight *= 0.2 + 0.8 * max(0.0, min(1.0, age / 14))
        except (TypeError, ValueError, OverflowError):
            pass
    return weight
