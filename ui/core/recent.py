from datetime import datetime
from core.settings import load_all_settings, save_all_settings

def add_to_recent_songs(audio_path, lrc_path=None):
    """Add a song to recent list, keeping only 20 most recent."""
    payload = load_all_settings()
    recent = payload.get("recent_songs", [])
    
    # Remove if already exists
    recent = [r for r in recent if r.get("audio") != str(audio_path)]
    
    # Add to front
    recent.insert(0, {
        "audio": str(audio_path),
        "lrc": str(lrc_path) if lrc_path else None,
        "timestamp": datetime.now().isoformat(),
    })
    
    # Keep only 20
    payload["recent_songs"] = recent[:20]
    save_all_settings(payload)


def get_recent_songs():
    """Get list of recently played songs."""
    payload = load_all_settings()
    return payload.get("recent_songs", [])

