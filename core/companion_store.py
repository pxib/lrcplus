"""Local companion chats and user-authored memories, without provider credentials."""

import json
from pathlib import Path

from core import settings
from core.file_io import atomic_write_text


class CompanionStore:
    def __init__(self, path=None):
        self.path = Path(path) if path else settings.get_app_data_dir() / "companion.json"
        self.data = {"version": 1, "conversations": [], "memories": [], "memories_enabled": True}
        self.error = ""
        try:
            if self.path.exists():
                if self.path.stat().st_size > 32 * 1024 * 1024:
                    raise ValueError
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("version") != 1:
                    raise ValueError
                if not isinstance(data.get("conversations"), list):
                    raise ValueError
                ids = set()
                for chat in data["conversations"]:
                    if (not isinstance(chat, dict) or not isinstance(chat.get("id"), str)
                            or chat["id"] in ids or not isinstance(chat.get("title"), str)
                            or not isinstance(chat.get("exchanges"), list)):
                        raise ValueError
                    ids.add(chat["id"])
                    for item in chat["exchanges"]:
                        if (not isinstance(item, dict) or not isinstance(item.get("user"), str)
                                or not isinstance(item.get("reply"), (str, type(None)))
                                or not isinstance(item.get("expression"), (str, type(None)))):
                            raise ValueError
                validate_memories(data.get("memories", []))
                if type(data.get("memories_enabled", True)) is not bool:
                    raise ValueError
                if data.get("auto_memory_mode", "suggest") not in {"off", "suggest", "auto"}:
                    raise ValueError
                for name in ("memory_sources", "memory_checked_through"):
                    if not isinstance(data.get(name, {}), dict):
                        raise ValueError
                if any(not isinstance(source, dict) for source in data.get("memory_sources", {}).values()):
                    raise ValueError
                if any(type(value) is not int or value < 0 for value in data.get("memory_checked_through", {}).values()):
                    raise ValueError
                suggestions = data.get("memory_suggestions", [])
                if not isinstance(suggestions, list) or len(suggestions) > 50:
                    raise ValueError
                for item in suggestions:
                    if (not isinstance(item, dict) or any(not isinstance(item.get(key), str)
                            for key in ("id", "fact", "topic", "evidence", "chat_id", "category"))
                            or not 1 <= len(item["fact"]) <= 200 or type(item.get("page")) is not int
                            or not isinstance(item.get("replaces"), (str, type(None)))):
                        raise ValueError
                dismissed = data.get("dismissed_memories", [])
                if not isinstance(dismissed, list) or len(dismissed) > 200 or any(not isinstance(fact, str) for fact in dismissed):
                    raise ValueError
                self.data = data
        except (OSError, ValueError, TypeError, RecursionError):
            self.error = "Could not read saved chats/memories. The original file has been kept; saving is disabled."

    def save(self):
        if self.error:
            raise ValueError(self.error)
        text = json.dumps(self.data, ensure_ascii=False, indent=2)
        if len(text.encode("utf-8")) > 32 * 1024 * 1024:
            raise ValueError("Saved conversations are too large. Delete an older conversation to make room.")
        atomic_write_text(self.path, text)


def validate_memories(memories):
    if (not isinstance(memories, list) or len(memories) > 20
            or any(not isinstance(fact, str) or not fact.strip() or len(fact) > 200 for fact in memories)):
        raise ValueError("Use up to 20 memories, each at most 200 characters, one fact per line.")
    return [fact.strip() for fact in memories]
