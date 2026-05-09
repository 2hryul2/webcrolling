"""Tests for monitor/worker.py — duplicates and collector isolation."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from app.database import compute_content_hash, load_jsonl
from app.models import ExternalEvent
from monitor.worker import Worker


@pytest.fixture
def sources_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "sources.yaml"
    p.write_text(
        """
sources:
  dart:
    name: "DART"
    url: "https://x"
    poll_interval_seconds: 300
    timeout_seconds: 5
    retry_attempts: 1
    enabled: true
  fsc:
    name: "FSC"
    url: "https://y"
    poll_interval_seconds: 600
    timeout_seconds: 5
    retry_attempts: 1
    enabled: true
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def keywords_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "keywords.yaml"
    p.write_text(
        """
keywords:
  urgent:
    - "구조조정"
  watch: []
  info:
    - "공시"
""",
        encoding="utf-8",
    )
    return p


def _make_event(source: str, idx: int) -> ExternalEvent:
    title = f"Title-{source}-{idx}"
    url = f"https://{source}.example.com/{idx}"
    return ExternalEvent(
        source=source,
        external_id=f"{source}-{idx}",
        title=title,
        url=url,
        published_at=datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 9, 10, 5, tzinfo=timezone.utc),
        summary=None,
        raw_payload={},
        content_hash=compute_content_hash(title, url),
        severity="info",
    )


def test_duplicate_event_written_with_flag(
    sources_yaml, keywords_yaml, tmp_data_dir, monkeypatch
):
    """C3 — same event twice → second write has is_duplicate=true, only 1 notify."""
    worker = Worker(
        config_path=str(sources_yaml),
        keywords_path=str(keywords_yaml),
        data_dir=str(tmp_data_dir),
        smtp_config={},
    )

    notify_calls: list[str] = []

    def fake_notify(event):
        notify_calls.append(event.external_id)

        class _Log:
            status = "sent"

        return _Log()

    worker.notifier.notify = fake_notify  # type: ignore[assignment]

    e1 = _make_event("dart", 1)
    # Patch each collector to deliver the same event in two runs.
    dart = next(c for c in worker.collectors if c.source_id == "dart")
    fsc = next(c for c in worker.collectors if c.source_id == "fsc")

    with patch.object(dart, "collect", return_value=[e1]), patch.object(
        fsc, "collect", return_value=[]
    ):
        first = worker.run_once()
    with patch.object(dart, "collect", return_value=[e1]), patch.object(
        fsc, "collect", return_value=[]
    ):
        second = worker.run_once()

    assert first == 1
    assert second == 0
    rows = load_jsonl(str(tmp_data_dir / "events.jsonl"))
    assert len(rows) == 2
    # First fresh, second duplicate.
    assert rows[0]["is_duplicate"] is False
    assert rows[1]["is_duplicate"] is True
    # Only one notify call (the fresh one).
    assert notify_calls == ["dart-1"]


def test_collector_failure_isolated(
    sources_yaml, keywords_yaml, tmp_data_dir, monkeypatch
):
    """T2 — one source raises, other source still processes (ThreadPoolExecutor)."""
    worker = Worker(
        config_path=str(sources_yaml),
        keywords_path=str(keywords_yaml),
        data_dir=str(tmp_data_dir),
        smtp_config={},
    )
    worker.notifier.notify = lambda e: None  # type: ignore[assignment]

    dart = next(c for c in worker.collectors if c.source_id == "dart")
    fsc = next(c for c in worker.collectors if c.source_id == "fsc")
    e_fsc = _make_event("fsc", 1)

    def _raise():
        raise RuntimeError("DART boom")

    with patch.object(dart, "collect", side_effect=_raise), patch.object(
        fsc, "collect", return_value=[e_fsc]
    ):
        n = worker.run_once()

    assert n == 1
    rows = load_jsonl(str(tmp_data_dir / "events.jsonl"))
    assert len(rows) == 1
    assert rows[0]["source"] == "fsc"
    # Error counter incremented for dart
    assert worker.error_counts.get("dart", 0) == 1


def test_matched_keywords_always_list_even_when_empty(
    sources_yaml, keywords_yaml, tmp_data_dir
):
    """C16 — even when no keyword matched, matched_keywords is [] not None."""
    worker = Worker(
        config_path=str(sources_yaml),
        keywords_path=str(keywords_yaml),
        data_dir=str(tmp_data_dir),
        smtp_config={},
    )
    worker.notifier.notify = lambda e: None  # type: ignore[assignment]

    e_no_match = _make_event("dart", 99)  # title "Title-dart-99" matches nothing
    dart = next(c for c in worker.collectors if c.source_id == "dart")
    fsc = next(c for c in worker.collectors if c.source_id == "fsc")
    with patch.object(dart, "collect", return_value=[e_no_match]), patch.object(
        fsc, "collect", return_value=[]
    ):
        worker.run_once()

    rows = load_jsonl(str(tmp_data_dir / "events.jsonl"))
    assert len(rows) == 1
    assert rows[0]["matched_keywords"] == []  # empty list, not None
    assert rows[0]["is_duplicate"] is False


def test_matched_keywords_recorded_when_present(
    sources_yaml, keywords_yaml, tmp_data_dir
):
    """Sanity — when title contains an urgent keyword, severity=urgent and list populated."""
    worker = Worker(
        config_path=str(sources_yaml),
        keywords_path=str(keywords_yaml),
        data_dir=str(tmp_data_dir),
        smtp_config={},
    )
    worker.notifier.notify = lambda e: None  # type: ignore[assignment]

    title = "긴급 구조조정 보고"
    url = "https://dart.example.com/urgent"
    e = ExternalEvent(
        source="dart",
        external_id="dart-u1",
        title=title,
        url=url,
        published_at=datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 9, 10, 5, tzinfo=timezone.utc),
        summary=None,
        raw_payload={},
        content_hash=compute_content_hash(title, url),
        severity="info",
    )
    dart = next(c for c in worker.collectors if c.source_id == "dart")
    fsc = next(c for c in worker.collectors if c.source_id == "fsc")
    with patch.object(dart, "collect", return_value=[e]), patch.object(
        fsc, "collect", return_value=[]
    ):
        worker.run_once()
    rows = load_jsonl(str(tmp_data_dir / "events.jsonl"))
    assert len(rows) == 1
    assert rows[0]["severity"] == "urgent"
    assert "구조조정" in rows[0]["matched_keywords"]
