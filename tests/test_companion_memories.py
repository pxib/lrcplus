import json

import pytest

from core.companion_memories import apply_candidates, build_memory_request, normalized, parse_candidates


MESSAGES = [{"page": 3, "text": "I prefer short replies. My favorite genre is jazz."}]


def candidate(**changes):
    item = {"fact": "Prefers short replies", "category": "communication", "topic": "response_length",
            "source": 3, "evidence": "I prefer short replies.", "replaces": None}
    item.update(changes)
    return item


def test_extraction_request_contains_only_selected_user_messages_and_facts():
    url, headers, body = build_memory_request("https://example.com/v1", "model", "key", MESSAGES,
                                             ["Likes jazz"], ["dismissed"],
                                             {"service_tier": "flex", "tools": [], "response_format": {"type": "text"}})
    data = json.loads(body["messages"][1]["content"])
    assert data["messages"] == MESSAGES
    assert data["existing_facts"] == ["Likes jazz"]
    assert "Sakurabayashi" not in body["messages"][0]["content"]
    assert body["service_tier"] == "flex"
    assert "tools" not in body and "response_format" not in body


@pytest.mark.parametrize("changes", [{"source": 2}, {"evidence": "Not actually said"},
                                    {"category": "health"}, {"fact": "API key sk-secret"},
                                    {"fact": "Email: user@example.com"}, {"replaces": "invented existing fact"}])
def test_extraction_rejects_unverifiable_or_disallowed_candidates(changes):
    assert parse_candidates(json.dumps([candidate(**changes)]), MESSAGES, [], [], "chat") == []


def test_duplicates_and_dismissals_are_not_suggested_again():
    text = json.dumps([candidate(), candidate()])
    assert len(parse_candidates(text, MESSAGES, [], [], "chat")) == 1
    assert parse_candidates(text, MESSAGES, ["Prefers short replies."], [], "chat") == []
    assert parse_candidates(text, MESSAGES, [], [normalized("Prefers short replies")], "chat") == []


def test_auto_mode_saves_ordinary_preferences_but_queues_conflicts_and_unknown_topics():
    items = parse_candidates(json.dumps([candidate()]), MESSAGES, [], [], "chat")
    data = {"auto_memory_mode": "auto", "memories": []}
    apply_candidates(data, items)
    assert data["memories"] == ["Prefers short replies"]
    assert data["memory_sources"]["Prefers short replies"]["page"] == 3
    conflict = {**items[0], "id": "different", "fact": "Prefers long replies", "replaces": "Prefers short replies"}
    apply_candidates(data, [conflict])
    assert data["memories"] == ["Prefers short replies"]
    assert data["memory_suggestions"][0]["fact"] == "Prefers long replies"
    manual = {"auto_memory_mode": "auto", "memories": ["A manually written preference"]}
    apply_candidates(manual, items)
    assert manual["memories"] == ["A manually written preference"]
    assert len(manual["memory_suggestions"]) == 1


def test_suggest_mode_never_saves_without_approval_and_limits_pending_queue():
    items = parse_candidates(json.dumps([candidate()]), MESSAGES, [], [], "chat")
    data = {"memories": []}
    apply_candidates(data, items)
    apply_candidates(data, items)
    assert data["memories"] == []
    assert len(data["memory_suggestions"]) == 1
