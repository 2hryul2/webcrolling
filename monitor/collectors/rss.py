"""Base RSS collector — fetch + parse RSS/Atom feeds into ExternalEvent."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from time import mktime
from typing import Any, Optional

import feedparser

from app.database import compute_content_hash
from app.models import ExternalEvent

logger = logging.getLogger(__name__)

USER_AGENT = "claude_webcroll/1.0"


class RSSCollector:
    """Generic RSS/Atom collector.

    Subclasses may override `collect()` to add filtering or per-source logic.
    """

    def __init__(self, source_id: str, name: str, endpoint: str) -> None:
        self.source_id = source_id
        self.name = name
        self.endpoint = endpoint

    def fetch(self) -> list[dict[str, Any]]:
        """Fetch and return raw entries from the RSS endpoint."""
        try:
            parsed = feedparser.parse(
                self.endpoint,
                request_headers={"User-Agent": USER_AGENT},
            )
        except Exception as exc:
            logger.warning("[%s] feedparser.parse failed: %s", self.source_id, exc)
            return []

        if getattr(parsed, "bozo", 0) and parsed.entries == []:
            logger.warning(
                "[%s] feed parse error: %s", self.source_id, getattr(parsed, "bozo_exception", "?")
            )
            return []

        return list(parsed.entries or [])

    def parse(self, raw_entries: list[dict[str, Any]]) -> list[ExternalEvent]:
        """Normalize raw feedparser entries to ExternalEvent objects."""
        now = datetime.now(timezone.utc)
        events: list[ExternalEvent] = []

        for entry in raw_entries:
            try:
                title = (entry.get("title") or "").strip()
                url = (entry.get("link") or "").strip()
                if not title or not url:
                    continue

                external_id = (
                    entry.get("id")
                    or entry.get("guid")
                    or url
                )
                external_id = str(external_id)

                published_at = self._extract_datetime(entry) or now
                summary = entry.get("summary") or entry.get("description") or None

                # raw_payload — keep something serializable.
                raw_payload: dict[str, Any] = {}
                for key in ("title", "link", "id", "guid", "summary", "description", "published", "updated"):
                    if entry.get(key) is not None:
                        raw_payload[key] = entry.get(key)

                content_hash = compute_content_hash(title, url)

                event = ExternalEvent(
                    source=self.source_id,
                    external_id=external_id,
                    title=title,
                    url=url,
                    published_at=published_at,
                    fetched_at=now,
                    summary=summary,
                    raw_payload=raw_payload,
                    content_hash=content_hash,
                    severity="info",
                )
                events.append(event)
            except Exception as exc:
                logger.warning("[%s] parse entry failed: %s", self.source_id, exc)
                continue

        return events

    def collect(self) -> list[ExternalEvent]:
        """Fetch + parse with try/except — returns [] on failure."""
        try:
            raw = self.fetch()
            return self.parse(raw)
        except Exception as exc:
            logger.warning("[%s] collect failed: %s", self.source_id, exc)
            return []

    @staticmethod
    def _extract_datetime(entry: dict[str, Any]) -> Optional[datetime]:
        """Pull a datetime from feedparser entry — try parsed structs first."""
        for key in ("published_parsed", "updated_parsed"):
            value = entry.get(key)
            if value:
                try:
                    return datetime.fromtimestamp(mktime(value), tz=timezone.utc)
                except Exception:
                    continue

        for key in ("published", "updated"):
            value = entry.get(key)
            if value and isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except Exception:
                    continue

        return None
