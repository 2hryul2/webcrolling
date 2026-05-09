"""Base RSS collector — fetch + parse RSS/Atom feeds into ExternalEvent."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from time import mktime
from typing import Any, Optional

import feedparser
import httpx

from app.database import compute_content_hash
from app.models import ExternalEvent

logger = logging.getLogger(__name__)

USER_AGENT = "claude_webcroll/1.0"


class RSSCollector:
    """Generic RSS/Atom collector.

    Subclasses may override `collect()` to add filtering or per-source logic.
    """

    def __init__(
        self,
        source_id: str,
        name: str,
        endpoint: str,
        timeout_seconds: int = 30,
        retry_attempts: int = 3,
    ) -> None:
        self.source_id = source_id
        self.name = name
        self.endpoint = endpoint
        # Bound timeout to a sane range; spec section 7 caps it at < 60.
        self.timeout_seconds = max(1, int(timeout_seconds))
        self.retry_attempts = max(1, int(retry_attempts))

    def fetch(self) -> list[dict[str, Any]]:
        """Fetch the RSS endpoint with retries and per-source timeout.

        Spec FR-1 / 3.1.2: 3 attempts, exponential backoff (1s, 2s, 4s),
        per-source `timeout_seconds`. Returns raw feedparser entries; on
        exhaustion of attempts returns []. Network bytes are fetched via
        httpx (so timeout actually applies), then handed to feedparser.parse.
        """
        attempts = self.retry_attempts
        backoffs = [1, 2, 4]
        last_exc: Optional[BaseException] = None
        body: Optional[bytes] = None

        for attempt_idx in range(attempts):
            try:
                with httpx.Client(
                    timeout=self.timeout_seconds,
                    follow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                ) as client:
                    resp = client.get(self.endpoint)
                if resp.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                body = resp.content
                break
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "[%s] fetch attempt %d/%d failed: %s",
                    self.source_id,
                    attempt_idx + 1,
                    attempts,
                    type(exc).__name__,
                )
                if attempt_idx < attempts - 1:
                    backoff = backoffs[attempt_idx] if attempt_idx < len(backoffs) else backoffs[-1]
                    time.sleep(backoff)

        if body is None:
            logger.warning(
                "[%s] fetch failed after %d attempts: %s",
                self.source_id,
                attempts,
                type(last_exc).__name__ if last_exc else "unknown",
            )
            return []

        try:
            parsed = feedparser.parse(body)
        except Exception as exc:
            logger.warning("[%s] feedparser.parse failed: %s", self.source_id, exc)
            return []

        if getattr(parsed, "bozo", 0) and not (parsed.entries or []):
            logger.warning(
                "[%s] feed parse error: %s",
                self.source_id,
                getattr(parsed, "bozo_exception", "?"),
            )
            return []

        return list(parsed.entries or [])

    def parse(self, raw_entries: list[dict[str, Any]]) -> list[ExternalEvent]:
        """Normalize raw feedparser entries to ExternalEvent objects."""
        now = datetime.now(timezone.utc)
        events: list[ExternalEvent] = []

        for entry in raw_entries:
            try:
                title = (self._entry_get(entry, "title") or "").strip()
                url = (self._entry_get(entry, "link") or "").strip()
                if not title or not url:
                    continue

                external_id = (
                    self._entry_get(entry, "id")
                    or self._entry_get(entry, "guid")
                    or url
                )
                external_id = str(external_id)

                published_at = self._extract_datetime(entry) or now
                summary = (
                    self._entry_get(entry, "summary")
                    or self._entry_get(entry, "description")
                    or None
                )

                # raw_payload — keep something serializable.
                raw_payload: dict[str, Any] = {}
                for key in (
                    "title",
                    "link",
                    "id",
                    "guid",
                    "summary",
                    "description",
                    "published",
                    "updated",
                ):
                    v = self._entry_get(entry, key)
                    if v is not None:
                        raw_payload[key] = v

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
    def _entry_get(entry: Any, key: str) -> Any:
        """feedparser entries are FeedParserDict — support both attr and key."""
        if isinstance(entry, dict):
            return entry.get(key)
        # FeedParserDict supports .get; fall back to getattr.
        getter = getattr(entry, "get", None)
        if callable(getter):
            try:
                return getter(key)
            except Exception:
                pass
        return getattr(entry, key, None)

    @classmethod
    def _extract_datetime(cls, entry: Any) -> Optional[datetime]:
        """Pull a datetime from feedparser entry — try parsed structs first."""
        for key in ("published_parsed", "updated_parsed"):
            value = cls._entry_get(entry, key)
            if value:
                try:
                    return datetime.fromtimestamp(mktime(value), tz=timezone.utc)
                except Exception:
                    continue

        for key in ("published", "updated"):
            value = cls._entry_get(entry, key)
            if value and isinstance(value, str):
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except Exception:
                    continue

        return None
