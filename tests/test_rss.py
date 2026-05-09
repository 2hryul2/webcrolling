"""Tests for the RSS collector base + DART subclass.

Network calls are mocked — we only verify parsing and filtering logic.
"""

from __future__ import annotations

from unittest.mock import patch

from monitor.collectors.dart import DARTCollector
from monitor.collectors.fsc import FSCCollector
from monitor.collectors.rss import RSSCollector


def test_rss_parse_rss20(mock_rss_entries):
    collector = RSSCollector(source_id="test", name="Test", endpoint="https://x")
    events = collector.parse(mock_rss_entries)
    assert len(events) == 2
    assert events[0].source == "test"
    assert events[0].title == "샘플 RSS 제목"
    assert events[0].url == "https://example.com/rss/1"
    assert events[0].external_id == "rss-1"
    assert events[0].content_hash  # deterministic


def test_rss_parse_atom(mock_atom_entries):
    collector = RSSCollector(source_id="atom", name="Atom Feed", endpoint="https://x")
    events = collector.parse(mock_atom_entries)
    assert len(events) == 1
    e = events[0]
    assert e.title == "Atom Sample Entry"
    assert e.url == "https://example.com/atom/1"
    # Should pick up the updated_parsed datetime
    assert e.published_at.year == 2026


def test_rss_parse_skips_entries_missing_title_or_url():
    collector = RSSCollector(source_id="t", name="t", endpoint="https://x")
    raw = [
        {"title": "", "link": "https://example.com/x"},
        {"title": "Only title"},
        {"title": "Valid", "link": "https://example.com/v", "id": "v"},
    ]
    events = collector.parse(raw)
    assert len(events) == 1
    assert events[0].title == "Valid"


def test_rss_collect_handles_fetch_failure():
    collector = RSSCollector(source_id="t", name="t", endpoint="https://x")

    with patch.object(RSSCollector, "fetch", side_effect=Exception("network down")):
        events = collector.collect()
    assert events == []


def test_rss_fetch_returns_empty_on_parse_error():
    """Simulate feedparser failure → fetch should return []."""
    collector = RSSCollector(source_id="t", name="t", endpoint="https://x")
    with patch("monitor.collectors.rss.feedparser.parse", side_effect=Exception("boom")):
        result = collector.fetch()
    assert result == []


def test_dart_collector_no_watchlist_keeps_all(mock_rss_entries, monkeypatch):
    monkeypatch.delenv("DART_WATCHLIST", raising=False)
    collector = DARTCollector(name="DART", endpoint="https://x", watchlist=[])
    with patch.object(RSSCollector, "fetch", return_value=mock_rss_entries):
        events = collector.collect()
    assert len(events) == 2
    assert all(e.source == "dart" for e in events)


def test_dart_collector_watchlist_filters(monkeypatch):
    monkeypatch.delenv("DART_WATCHLIST", raising=False)
    raw = [
        {
            "title": "Match company corp_code 00111111 announcement",
            "link": "https://example.com/dart/1",
            "id": "dart-1",
            "summary": "00111111 details",
            "published_parsed": (2026, 5, 9, 10, 0, 0, 5, 129, 0),
        },
        {
            "title": "Unrelated company",
            "link": "https://example.com/dart/2",
            "id": "dart-2",
            "summary": "no codes here",
            "published_parsed": (2026, 5, 9, 10, 5, 0, 5, 129, 0),
        },
    ]
    collector = DARTCollector(name="DART", endpoint="https://x", watchlist=["00111111"])
    with patch.object(RSSCollector, "fetch", return_value=raw):
        events = collector.collect()
    assert len(events) == 1
    assert events[0].external_id == "dart-1"


def test_fsc_collector_basic(mock_rss_entries):
    collector = FSCCollector(name="FSC", endpoint="https://x")
    with patch.object(RSSCollector, "fetch", return_value=mock_rss_entries):
        events = collector.collect()
    assert len(events) == 2
    assert all(e.source == "fsc" for e in events)
