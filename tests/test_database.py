"""Tests for app/database.py JSONL helpers."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from app.database import (
    append_jsonl,
    compute_content_hash,
    event_exists,
    load_existing_hashes,
    load_jsonl,
    load_state,
    save_state,
)
from app.models import ExternalEvent


def _make_event(idx: int) -> ExternalEvent:
    return ExternalEvent(
        source="dart",
        external_id=f"id-{idx}",
        title=f"Title {idx}",
        url=f"https://example.com/{idx}",
        published_at=datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 9, 10, 5, tzinfo=timezone.utc),
        summary=None,
        raw_payload={},
        content_hash=compute_content_hash(f"Title {idx}", f"https://example.com/{idx}"),
        severity="info",
    )


def test_append_and_load_jsonl(tmp_data_dir):
    path = str(tmp_data_dir / "events.jsonl")
    e1 = _make_event(1)
    e2 = _make_event(2)
    assert append_jsonl(path, e1) is True
    assert append_jsonl(path, e2) is True

    rows = load_jsonl(path)
    assert len(rows) == 2
    assert rows[0]["external_id"] == "id-1"
    assert rows[1]["external_id"] == "id-2"


def test_load_jsonl_with_limit(tmp_data_dir):
    path = str(tmp_data_dir / "events.jsonl")
    for i in range(5):
        append_jsonl(path, _make_event(i))
    last_two = load_jsonl(path, limit=2)
    assert len(last_two) == 2
    assert last_two[0]["external_id"] == "id-3"
    assert last_two[1]["external_id"] == "id-4"


def test_load_jsonl_skips_corrupted(tmp_data_dir):
    path = str(tmp_data_dir / "events.jsonl")
    append_jsonl(path, _make_event(1))
    # Manually inject a broken line
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not json}\n")
    append_jsonl(path, _make_event(2))

    rows = load_jsonl(path)
    assert len(rows) == 2
    assert {r["external_id"] for r in rows} == {"id-1", "id-2"}


def test_load_jsonl_missing_returns_empty(tmp_data_dir):
    rows = load_jsonl(str(tmp_data_dir / "nope.jsonl"))
    assert rows == []


def test_save_and_load_state(tmp_data_dir):
    path = str(tmp_data_dir / "state.json")
    state = {"last_poll": {"dart": "2026-05-09T10:00:00+00:00"}, "event_count": 42}
    save_state(path, state)
    assert os.path.exists(path)
    loaded = load_state(path)
    assert loaded["event_count"] == 42
    assert loaded["last_poll"]["dart"] == "2026-05-09T10:00:00+00:00"


def test_load_state_missing_returns_empty(tmp_data_dir):
    assert load_state(str(tmp_data_dir / "missing.json")) == {}


def test_compute_content_hash_deterministic():
    h1 = compute_content_hash("title", "https://x")
    h2 = compute_content_hash("title", "https://x")
    h3 = compute_content_hash("title", "https://y")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 32  # MD5 hex


def test_event_exists_with_cache():
    cache = {"abc"}
    assert event_exists("abc", cache) is True
    assert event_exists("def", cache) is False


def test_load_existing_hashes(tmp_data_dir):
    path = str(tmp_data_dir / "events.jsonl")
    append_jsonl(path, _make_event(1))
    append_jsonl(path, _make_event(2))
    cache = load_existing_hashes(path)
    assert len(cache) == 2
