"""DART RSS collector with optional watchlist filtering."""

from __future__ import annotations

import logging
import os
from typing import Optional

from app.models import ExternalEvent
from monitor.collectors.rss import RSSCollector

logger = logging.getLogger(__name__)

# Step 2 deferred: exact corp_code field-match. Step 1 uses substring.
# We log this once per process to make the limitation visible.
_DART_WATCHLIST_WARNED = False


def _warn_substring_match_once() -> None:
    global _DART_WATCHLIST_WARNED
    if not _DART_WATCHLIST_WARNED:
        logger.warning(
            "DART watchlist using substring match — exact corp_code match deferred to Step 2"
        )
        _DART_WATCHLIST_WARNED = True


class DARTCollector(RSSCollector):
    """DART RSS collector with corp_code watchlist support."""

    def __init__(
        self,
        name: str,
        endpoint: str,
        watchlist: Optional[list[str]] = None,
        timeout_seconds: int = 30,
        retry_attempts: int = 3,
    ) -> None:
        super().__init__(
            source_id="dart",
            name=name,
            endpoint=endpoint,
            timeout_seconds=timeout_seconds,
            retry_attempts=retry_attempts,
        )
        if watchlist is None:
            env = os.getenv("DART_WATCHLIST", "").strip()
            watchlist = [c.strip() for c in env.split(",") if c.strip()] if env else []
        self.watchlist: list[str] = watchlist or []
        if self.watchlist:
            _warn_substring_match_once()

    def collect(self) -> list[ExternalEvent]:
        """Collect events and filter by watchlist if configured."""
        events = super().collect()

        if not self.watchlist:
            return events

        filtered: list[ExternalEvent] = []
        for event in events:
            haystack_parts = [event.title or "", event.url or "", event.summary or ""]
            for key in ("id", "guid", "link", "title", "summary", "description"):
                v = event.raw_payload.get(key)
                if v:
                    haystack_parts.append(str(v))
            haystack = " ".join(haystack_parts)
            if any(code in haystack for code in self.watchlist):
                filtered.append(event)

        logger.info(
            "[dart] watchlist filter: %d/%d events kept", len(filtered), len(events)
        )
        return filtered
