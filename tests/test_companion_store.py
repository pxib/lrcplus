import json

import pytest

from core.companion_store import CompanionStore, validate_memories


def test_corrupt_storage_is_preserved_and_not_overwritten(tmp_path):
    path = tmp_path / "companion.json"
    path.write_text("broken content", encoding="utf-8")
    store = CompanionStore(path)
    assert store.error
    with pytest.raises(ValueError):
        store.save()
    assert path.read_text(encoding="utf-8") == "broken content"


@pytest.mark.parametrize("facts", [["x" * 201], ["fact"] * 21, [None], "name"])
def test_memory_budget_rejects_oversized_or_invalid_facts(facts):
    with pytest.raises(ValueError):
        validate_memories(facts)


def test_memory_storage_round_trip(tmp_path):
    path = tmp_path / "companion.json"
    store = CompanionStore(path)
    store.data.update(memories=["Call me maiko"], memories_enabled=False)
    store.save()
    reopened = CompanionStore(path)
    assert reopened.data["memories"] == ["Call me maiko"]
    assert reopened.data["memories_enabled"] is False
