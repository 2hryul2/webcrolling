"""Tests for the RSS collector base + DART subclass.

Network calls are mocked — we only verify parsing, retry, and filtering logic.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import feedparser
import httpx
import pytest

from monitor.collectors.dart import DARTCollector
from monitor.collectors.fsc import FSCCollector
from monitor.collectors.rss import RSSCollector


# ---------- existing parse / collect behavior ----------


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


def test_rss_fetch_returns_empty_on_parse_error(monkeypatch):
    """If feedparser raises after a successful HTTP fetch, return []."""
    collector = RSSCollector(
        source_id="t", name="t", endpoint="https://x", retry_attempts=1
    )

    fake_response = MagicMock(status_code=200, content=b"not really xml")
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_response

    monkeypatch.setattr(
        "monitor.collectors.rss.httpx.Client", lambda **kw: fake_client
    )
    monkeypatch.setattr(
        "monitor.collectors.rss.feedparser.parse", lambda body: (_ for _ in ()).throw(Exception("boom"))
    )
    result = collector.fetch()
    assert result == []


# ---------- C1: retries + timeout ----------


def test_rss_fetch_retries_three_times_then_returns_empty(monkeypatch):
    """All 3 attempts fail → fetch returns [], no sleep waits in tests."""
    collector = RSSCollector(
        source_id="t", name="t", endpoint="https://x", retry_attempts=3
    )

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.side_effect = httpx.ConnectError("timeout", request=MagicMock())

    monkeypatch.setattr(
        "monitor.collectors.rss.httpx.Client", lambda **kw: fake_client
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr("monitor.collectors.rss.time.sleep", lambda s: sleep_calls.append(s))

    result = collector.fetch()
    assert result == []
    # 3 GET attempts, 2 sleeps between them (1s, 2s — matches FR-1 backoff).
    assert fake_client.get.call_count == 3
    assert sleep_calls == [1, 2]


def test_rss_fetch_retry_then_succeed(monkeypatch):
    """First attempt fails, second succeeds — no third call."""
    collector = RSSCollector(
        source_id="t", name="t", endpoint="https://x", retry_attempts=3
    )

    rss_xml = (
        b"<?xml version='1.0'?><rss version='2.0'><channel>"
        b"<title>x</title>"
        b"<item><title>OK</title><link>https://example.com/1</link>"
        b"<guid>g1</guid></item>"
        b"</channel></rss>"
    )
    ok = MagicMock(status_code=200, content=rss_xml)

    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.side_effect = [
        httpx.ConnectError("transient", request=MagicMock()),
        ok,
    ]

    monkeypatch.setattr("monitor.collectors.rss.httpx.Client", lambda **kw: fake_client)
    monkeypatch.setattr("monitor.collectors.rss.time.sleep", lambda s: None)

    result = collector.fetch()
    assert len(result) == 1
    assert fake_client.get.call_count == 2


# ---------- T4: real feedparser end-to-end on small fixture XML ----------

DART_RSS20_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>DART today</title>
    <link>https://dart.fss.or.kr</link>
    <description>DART feed</description>
    <item>
      <title>한신금융그룹 구조조정 발표</title>
      <link>https://dart.fss.or.kr/123456</link>
      <guid>dart-123456</guid>
      <pubDate>Fri, 09 May 2026 10:30:00 GMT</pubDate>
      <description>정정 공시</description>
    </item>
    <item>
      <title>일반 공시 보고서</title>
      <link>https://dart.fss.or.kr/234567</link>
      <guid>dart-234567</guid>
      <pubDate>Fri, 09 May 2026 11:00:00 GMT</pubDate>
      <description>주요 공시 사항</description>
    </item>
  </channel>
</rss>
"""

FSC_RSS20_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>FSC press</title>
    <link>https://www.fsc.go.kr</link>
    <description>FSC press releases</description>
    <item>
      <title>금융위 보도자료: 신규 정책 발표</title>
      <link>https://www.fsc.go.kr/press/100</link>
      <pubDate>2026-05-09T10:30:00+09:00</pubDate>
      <description>금융위 정책 안내</description>
    </item>
  </channel>
</rss>
"""


@pytest.mark.parametrize(
    "xml,source_id,expected_count,expected_first_title",
    [
        (DART_RSS20_XML, "dart", 2, "한신금융그룹 구조조정 발표"),
        (FSC_RSS20_XML, "fsc", 1, "금융위 보도자료: 신규 정책 발표"),
    ],
)
def test_feedparser_end_to_end_parse_to_external_event(
    xml, source_id, expected_count, expected_first_title
):
    """T4 — exercise real feedparser objects (FeedParserDict, struct_time)."""
    parsed = feedparser.parse(xml)
    assert parsed.entries, "feedparser should produce entries from valid RSS 2.0"
    collector = RSSCollector(source_id=source_id, name=source_id, endpoint="https://x")
    events = collector.parse(list(parsed.entries))
    assert len(events) == expected_count
    assert events[0].title == expected_first_title
    assert events[0].source == source_id
    assert events[0].published_at.year == 2026
    assert events[0].content_hash


def test_feedparser_end_to_end_via_fetch_with_mocked_http(monkeypatch):
    """T4 — full pipeline: httpx mocked → real feedparser → ExternalEvent."""
    collector = RSSCollector(
        source_id="dart", name="dart", endpoint="https://x", retry_attempts=1
    )

    fake_response = MagicMock(status_code=200, content=DART_RSS20_XML.encode("utf-8"))
    fake_client = MagicMock()
    fake_client.__enter__.return_value = fake_client
    fake_client.__exit__.return_value = False
    fake_client.get.return_value = fake_response
    monkeypatch.setattr("monitor.collectors.rss.httpx.Client", lambda **kw: fake_client)

    events = collector.collect()
    assert len(events) == 2
    assert events[0].title == "한신금융그룹 구조조정 발표"


# ---------- DART / FSC subclasses ----------


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
