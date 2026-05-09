"""Shared pytest fixtures for the webcroll test suite."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

# Ensure project root on sys.path so `from app...`, `from monitor...` work.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """Provide an isolated temporary data dir."""
    d = tmp_path / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def sample_event_dict() -> dict[str, Any]:
    return {
        "source": "dart",
        "external_id": "20260509-001",
        "title": "Sample 공시 보고서",
        "url": "https://dart.fss.or.kr/example/1",
        "published_at": datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
        "fetched_at": datetime(2026, 5, 9, 10, 5, tzinfo=timezone.utc),
        "summary": "샘플 요약",
        "raw_payload": {"k": "v"},
        "content_hash": "abc123",
        "severity": "info",
        "matched_keywords": None,
    }


@pytest.fixture
def sample_keywords_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "keywords.yaml"
    p.write_text(
        """
keywords:
  urgent:
    - "구조조정"
    - "파산"
  watch:
    - "인수합병"
    - "M&A"
  info:
    - "공시"
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def mock_rss_entries() -> list[dict[str, Any]]:
    """Two RSS-style entries simulating feedparser output."""
    return [
        {
            "title": "샘플 RSS 제목",
            "link": "https://example.com/rss/1",
            "id": "rss-1",
            "summary": "RSS 요약",
            "published": "Sat, 09 May 2026 10:00:00 +0000",
            "published_parsed": (2026, 5, 9, 10, 0, 0, 5, 129, 0),
        },
        {
            "title": "공시 알림",
            "link": "https://example.com/rss/2",
            "id": "rss-2",
            "summary": "공시 내용",
            "published": "Sat, 09 May 2026 11:00:00 +0000",
            "published_parsed": (2026, 5, 9, 11, 0, 0, 5, 129, 0),
        },
    ]


@pytest.fixture
def mock_atom_entries() -> list[dict[str, Any]]:
    """Atom-style entries — uses 'updated' instead of 'published'."""
    return [
        {
            "title": "Atom Sample Entry",
            "link": "https://example.com/atom/1",
            "id": "tag:example.com,2026:1",
            "summary": "atom summary",
            "updated": "2026-05-09T12:00:00+00:00",
            "updated_parsed": (2026, 5, 9, 12, 0, 0, 5, 129, 0),
        }
    ]
