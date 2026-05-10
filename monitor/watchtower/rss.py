"""RSS / Atom crawler.

Implementation notes:

- ``feedparser`` does its own HTTP via urllib but offers no timeout. We
  fetch via ``httpx`` (so we get FR-CRL-008's 30s ceiling + a real UA
  header) then hand the body bytes to ``feedparser.parse``.
- ``bozo`` flag — feedparser sets ``bozo=1`` for any parse warning. If the
  feed yields ZERO entries AND ``bozo=1`` we treat it as failure. A
  ``bozo=1`` flag with N>0 entries is common (XML namespace nits) and
  considered a soft pass.
- timestamps — RSS uses ``published_parsed`` (UTC struct_time); Atom uses
  ``updated_parsed``. We honor whichever is present.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import feedparser
import httpx

from monitor.watchtower.base import (
    DEFAULT_TIMEOUT_SEC,
    USER_AGENT,
    CrawledItem,
    Crawler,
    CrawlResult,
)

logger = logging.getLogger(__name__)


def _struct_to_dt(t: time.struct_time | None) -> datetime | None:
    """Convert a feedparser ``time.struct_time`` to a tz-aware UTC datetime."""
    if not t:
        return None
    try:
        return datetime(*t[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _entry_to_item(entry: dict) -> CrawledItem | None:
    """Map a feedparser entry → ``CrawledItem``. Returns None on missing url/title."""
    title = (entry.get("title") or "").strip()
    url = (entry.get("link") or "").strip()
    if not url or not title:
        return None
    summary = entry.get("summary") or entry.get("description") or None
    if isinstance(summary, str):
        summary = summary.strip() or None
    published_at = _struct_to_dt(
        entry.get("published_parsed") or entry.get("updated_parsed")
    )
    # content_for_hash — title + url + summary (decision §6).
    parts = [title, url, summary or ""]
    return CrawledItem(
        title=title,
        url=url,
        summary=summary,
        published_at=published_at,
        content_for_hash="\n".join(parts),
    )


class RssCrawler(Crawler):
    """Fetch + parse an RSS/Atom feed using ``httpx`` + ``feedparser``."""

    def crawl(
        self,
        site,
        *,
        user_agent: str = USER_AGENT,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> CrawlResult:
        site_id = getattr(site, "id", "?")
        url = getattr(site, "url", "")
        result = CrawlResult(site_id=site_id)
        started = time.monotonic()

        if not url:
            result.error = "Site.url is empty"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        try:
            with httpx.Client(
                headers={"User-Agent": user_agent, "Accept": "application/rss+xml,application/atom+xml,application/xml;q=0.9,*/*;q=0.5"},
                timeout=timeout_sec,
                follow_redirects=True,
            ) as client:
                response = client.get(url)
        except Exception as exc:
            result.error = f"fetch failed: {type(exc).__name__}"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        if response.status_code != 200:
            result.error = f"HTTP {response.status_code}"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        body = response.content
        try:
            parsed = feedparser.parse(body)
        except Exception as exc:
            result.error = f"feedparser raised: {type(exc).__name__}"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        entries = parsed.get("entries") or []
        items: list[CrawledItem] = []
        for entry in entries:
            mapped = _entry_to_item(entry)
            if mapped is not None:
                items.append(mapped)

        # bozo handling: only fail when bozo=1 AND zero useful entries.
        if not items and parsed.get("bozo"):
            bozo_exc = parsed.get("bozo_exception")
            result.error = (
                f"feed parse error: {type(bozo_exc).__name__}"
                if bozo_exc is not None
                else "feed parse error (bozo=1, no entries)"
            )
        result.items = items
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result
