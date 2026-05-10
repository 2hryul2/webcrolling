"""robots.txt fetch + cache + ``is_allowed`` check.

Spec anchor: FR-SITE-005 — sites whose robots.txt forbids the Watchtower UA
on the target path are marked ``status='blocked'``. Decision §2 — the
fetch itself is fail-open: if robots.txt cannot be reached (404, timeout,
DNS failure) we treat the URL as allowed. This trades a small amount of
politeness for resilience on intranets / Cloudflare-fronted sites where
robots.txt is frequently absent.

Cache: per-domain ``RobotFileParser`` with a 6h TTL. The cache is process-
local (no shared store) — fine for a single uvicorn worker; revisit if we
go multi-process in Step 5.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser
from urllib.parse import urlparse

import httpx

from monitor.watchtower.base import DEFAULT_TIMEOUT_SEC, USER_AGENT

logger = logging.getLogger(__name__)

# 6h TTL — robots.txt rarely changes; reduces fetch volume per crawl pass.
_ROBOTS_TTL_SEC = 6 * 60 * 60

# domain → (expiry_epoch, parser, fetch_ok). ``fetch_ok=False`` means the
# fetch failed and we cached the failure so we don't retry every call.
_robots_cache: dict[str, tuple[float, urllib.robotparser.RobotFileParser, bool]] = {}
_cache_lock = threading.Lock()


def _domain(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _fetch_robots_text(robots_url: str, *, user_agent: str, timeout_sec: int) -> str | None:
    """Fetch robots.txt body. Returns ``None`` on any failure (fail-open)."""
    try:
        with httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout_sec,
            follow_redirects=True,
        ) as client:
            resp = client.get(robots_url)
        if resp.status_code != 200:
            logger.debug("robots.txt %s returned HTTP %s", robots_url, resp.status_code)
            return None
        return resp.text
    except Exception as exc:
        logger.debug("robots.txt fetch failed for %s: %s", robots_url, exc)
        return None


def _build_parser(domain: str, *, user_agent: str, timeout_sec: int) -> tuple[urllib.robotparser.RobotFileParser, bool]:
    """Fetch + parse robots.txt for ``domain``. Returns (parser, fetch_ok)."""
    robots_url = f"{domain}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    body = _fetch_robots_text(robots_url, user_agent=user_agent, timeout_sec=timeout_sec)
    if body is None:
        # Fail-open: empty parser = no rules = everything allowed.
        return parser, False
    parser.parse(body.splitlines())
    return parser, True


def is_allowed(
    url: str,
    user_agent: str = USER_AGENT,
    *,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> bool:
    """Return True iff ``url`` is allowed for ``user_agent`` per robots.txt.

    Fail-open: if robots.txt cannot be fetched or parsed, returns True.
    Returns False only when robots.txt was successfully fetched AND it has
    a Disallow rule matching the URL path.
    """
    if not url:
        return True
    domain = _domain(url)
    if not domain or not urlparse(url).netloc:
        return True

    now = time.time()
    with _cache_lock:
        cached = _robots_cache.get(domain)
        if cached and cached[0] > now:
            _expiry, parser, fetch_ok = cached
        else:
            parser, fetch_ok = _build_parser(
                domain, user_agent=user_agent, timeout_sec=timeout_sec
            )
            _robots_cache[domain] = (now + _ROBOTS_TTL_SEC, parser, fetch_ok)

    if not fetch_ok:
        return True  # explicit fail-open path

    try:
        return parser.can_fetch(user_agent, url)
    except Exception as exc:
        logger.debug("robots.can_fetch raised for %s: %s", url, exc)
        return True


def clear_cache() -> None:
    """Test helper — wipe the per-domain robots cache."""
    with _cache_lock:
        _robots_cache.clear()
