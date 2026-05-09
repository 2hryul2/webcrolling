"""Tests for deduplication via content_hash."""

from __future__ import annotations

from app.database import (
    append_jsonl,
    compute_content_hash,
    event_exists,
    load_existing_hashes,
)
from app.models import ExternalEvent
from datetime import datetime, timezone


def test_same_title_url_same_hash():
    h1 = compute_content_hash("같은 제목", "https://x.com/a")
    h2 = compute_content_hash("같은 제목", "https://x.com/a")
    assert h1 == h2


def test_different_url_different_hash():
    h1 = compute_content_hash("같은 제목", "https://x.com/a")
    h2 = compute_content_hash("같은 제목", "https://x.com/b")
    assert h1 != h2


def test_different_title_different_hash():
    h1 = compute_content_hash("제목 A", "https://x.com/a")
    h2 = compute_content_hash("제목 B", "https://x.com/a")
    assert h1 != h2


def test_event_exists_with_cache_set():
    cache = set()
    h = compute_content_hash("t", "u")
    assert event_exists("ignored", h, cache) is False
    cache.add(h)
    assert event_exists("ignored", h, cache) is True


def test_load_existing_hashes_round_trip(tmp_data_dir):
    path = str(tmp_data_dir / "events.jsonl")

    def _evt(idx: int) -> ExternalEvent:
        title = f"Title {idx}"
        url = f"https://example.com/{idx}"
        return ExternalEvent(
            source="dart",
            external_id=f"id-{idx}",
            title=title,
            url=url,
            published_at=datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
            content_hash=compute_content_hash(title, url),
            severity="info",
        )

    e1 = _evt(1)
    e2 = _evt(2)
    e3 = _evt(1)  # duplicate of e1 by title+url
    append_jsonl(path, e1)
    append_jsonl(path, e2)

    cache = load_existing_hashes(path)
    assert len(cache) == 2
    assert e1.content_hash in cache
    assert e2.content_hash in cache
    # e3 has same content_hash as e1
    assert e3.content_hash in cache
