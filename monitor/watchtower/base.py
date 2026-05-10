"""Watchtower crawler — common dataclasses and ``Crawler`` ABC.

Step 3 introduces the Watchtower crawler package separately from the
existing Step 1 ``monitor/collectors`` modules. This file defines the
shared types so ``rss.py`` / ``html.py`` / ``worker.py`` agree on the
shape of crawled data.

Spec anchors:

- FR-CRL-005 — every outbound HTTP request must carry the Watchtower
  User-Agent. Crawlers honor ``USER_AGENT`` here.
- FR-CRL-008 — every outbound HTTP request must use a 30s timeout.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-check only
    from app.db.models import Site


# Watchtower brand UA — FR-CRL-005. Sites with strict UA whitelists may need
# adjustment after Step 4 deploy review.
USER_AGENT = "Watchtower/1.0 (+https://watchtower.shinhan.local)"

# FR-CRL-008 — 30s timeout for any outbound HTTP request made by crawlers.
DEFAULT_TIMEOUT_SEC = 30


@dataclass
class CrawledItem:
    """A single item produced by a ``Crawler.crawl()`` pass.

    ``content_for_hash`` is the canonical input to SHA-256 in the detector
    (decision §6 — title + url + summary, NOT page body). CHANGE detection
    in Phase 2 will introduce a separate page-level hash.
    """

    title: str
    url: str
    summary: str | None
    published_at: datetime | None
    content_for_hash: str


@dataclass
class CrawlResult:
    """Outcome of a single ``crawl()`` invocation.

    ``error`` is set on any failure path; callers should treat any non-empty
    ``error`` as a failure even if ``items`` is non-empty (defensive).
    ``blocked_by_robots`` lets the worker mark ``Site.status='blocked'``
    without bumping the failure counter.
    """

    site_id: str
    items: list[CrawledItem] = field(default_factory=list)
    error: str | None = None
    blocked_by_robots: bool = False
    duration_ms: int = 0


class Crawler(ABC):
    """Abstract crawler interface — one concrete class per crawl_method."""

    @abstractmethod
    def crawl(
        self,
        site: "Site",
        *,
        user_agent: str = USER_AGENT,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> CrawlResult:
        """Fetch ``site`` and return a ``CrawlResult`` (never raises)."""
