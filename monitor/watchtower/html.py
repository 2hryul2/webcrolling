"""HTML crawler — httpx fetch + BeautifulSoup ``content_selector`` parse.

Decision §6 — only metadata (title + url + summary) feeds the content_hash.
Phase 2 will add a separate page-body hash for CHANGE detection.

Anchor extraction strategy:

- Apply ``site.content_selector`` to the parsed document.
- For each matching element, collect ``<a href>`` descendants.
- ``urljoin`` against the response URL so relative hrefs become absolute.
- Filter out javascript:/mailto:/empty hrefs and same-page anchors.
- Title := anchor text (stripped, single-line).
- Summary := nearest ancestor ``<li>`` / ``<tr>`` / ``<article>`` text,
  truncated to ~300 chars; falls back to anchor's parent text.
- Dedup within a single crawl by absolute URL.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from monitor.watchtower.base import (
    DEFAULT_TIMEOUT_SEC,
    USER_AGENT,
    CrawledItem,
    Crawler,
    CrawlResult,
)

logger = logging.getLogger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")
_SUMMARY_MAX = 300
_TITLE_MAX = 500


def _normalize(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text or "").strip()


def _is_useful_href(href: str) -> bool:
    if not href:
        return False
    href_lc = href.strip().lower()
    if href_lc.startswith("#"):
        return False
    if href_lc.startswith(("javascript:", "mailto:", "tel:")):
        return False
    return True


def _extract_summary(anchor: Tag) -> str | None:
    """Find an enclosing list-item / table-row / article and pull its text."""
    container_tags = ("li", "tr", "article", "p", "div")
    container = None
    for parent in anchor.parents:
        if isinstance(parent, Tag) and parent.name in container_tags:
            container = parent
            break
    if container is None:
        container = anchor.parent
    if container is None:
        return None
    text = _normalize(container.get_text(" ", strip=True))
    if not text:
        return None
    return text[:_SUMMARY_MAX]


class HtmlCrawler(Crawler):
    """Fetch + parse an HTML listing page with a CSS ``content_selector``."""

    def crawl(
        self,
        site,
        *,
        user_agent: str = USER_AGENT,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    ) -> CrawlResult:
        site_id = getattr(site, "id", "?")
        url = getattr(site, "url", "")
        selector = getattr(site, "content_selector", None) or None
        result = CrawlResult(site_id=site_id)
        started = time.monotonic()

        if not url:
            result.error = "Site.url is empty"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        try:
            with httpx.Client(
                headers={
                    "User-Agent": user_agent,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
                },
                timeout=timeout_sec,
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                response = client.get(url)
        except httpx.TimeoutException as exc:
            result.error = f"timeout: {type(exc).__name__}"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result
        except Exception as exc:
            result.error = f"fetch failed: {type(exc).__name__}"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        if response.status_code != 200:
            result.error = f"HTTP {response.status_code}"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception as exc:
            result.error = f"html parse failed: {type(exc).__name__}"
            result.duration_ms = int((time.monotonic() - started) * 1000)
            return result

        if selector:
            containers = soup.select(selector)
            if not containers:
                result.error = "content_selector matched no elements"
                result.duration_ms = int((time.monotonic() - started) * 1000)
                return result
        else:
            # No selector → search whole document; defensive fallback.
            containers = [soup]

        base_url = str(response.url)
        seen_urls: set[str] = set()
        items: list[CrawledItem] = []
        for container in containers:
            if not isinstance(container, Tag):
                continue
            for anchor in container.find_all("a"):
                if not isinstance(anchor, Tag):
                    continue
                href = anchor.get("href") or ""
                if not _is_useful_href(href):
                    continue
                absolute = urljoin(base_url, href)
                parsed = urlparse(absolute)
                if not parsed.scheme.startswith("http"):
                    continue
                if absolute in seen_urls:
                    continue
                title = _normalize(anchor.get_text(" ", strip=True))
                if not title:
                    # Use title attribute or aria-label as fallback.
                    title = _normalize(anchor.get("title") or anchor.get("aria-label") or "")
                if not title:
                    continue
                title = title[:_TITLE_MAX]
                summary = _extract_summary(anchor)
                seen_urls.add(absolute)
                content_for_hash = "\n".join([title, absolute, summary or ""])
                items.append(CrawledItem(
                    title=title,
                    url=absolute,
                    summary=summary,
                    published_at=None,
                    content_for_hash=content_for_hash,
                ))

        if not items and selector:
            # Selector matched the container, but no anchors inside.
            result.error = "content_selector matched no link items"

        result.items = items
        result.duration_ms = int((time.monotonic() - started) * 1000)
        return result
