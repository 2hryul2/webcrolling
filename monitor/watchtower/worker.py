"""WatchtowerWorker — coordinates per-site crawls.

Concurrency model (FR-CRL-006 / FR-CRL-007):

- One ``ThreadPoolExecutor`` (default 5 workers, ``WATCHTOWER_MAX_WORKERS``
  env override).
- Per-domain ``threading.Lock`` so two sites on the same host never fetch
  concurrently. Different domains crawl in parallel.
- ``_in_progress`` set keyed by ``site_id`` so an APScheduler tick that
  fires while the previous tick is still running returns immediately.

Failure handling (FR-SITE-006):

- Every ``run_site`` failure increments ``_failure_counters[site_id]``.
- On the 5th consecutive failure, ``Site.status='failed'`` and the worker
  emits a single ``logger.error(...)`` notification (Step 4 will replace
  this with real owner mail).
- ``_failure_notified`` ensures we don't spam the log on every subsequent
  tick — exactly one alert per failure streak.
- A successful run resets BOTH the counter and the notified flag.

The DB session lifecycle is short — one session per ``run_site`` call,
opened from the injected ``session_factory`` and committed at the end.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy import select

from app.db.models import Site
from monitor.watchtower import robots as robots_mod
from monitor.watchtower.base import (
    DEFAULT_TIMEOUT_SEC,
    USER_AGENT,
    Crawler,
    CrawlResult,
)
from monitor.watchtower.detector import detect_new_items
from monitor.watchtower.html import HtmlCrawler
from monitor.watchtower.rss import RssCrawler

logger = logging.getLogger(__name__)


# Number of consecutive failures before a Site is marked 'failed' (FR-SITE-006).
FAILURE_THRESHOLD = 5


def _crawler_for(method: str) -> Crawler:
    method_lc = (method or "").lower()
    if method_lc == "rss":
        return RssCrawler()
    if method_lc == "html":
        return HtmlCrawler()
    raise ValueError(f"unsupported crawl_method: {method!r}")


class WatchtowerWorker:
    """Thread-pool driven crawler runner for the Watchtower fleet."""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        max_workers: int | None = None,
        user_agent: str = USER_AGENT,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        crawler_factory: Callable[[str], Crawler] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._user_agent = user_agent
        self._timeout_sec = timeout_sec
        self._crawler_factory = crawler_factory or _crawler_for

        if max_workers is None:
            try:
                max_workers = int(os.getenv("WATCHTOWER_MAX_WORKERS", "5"))
            except ValueError:
                max_workers = 5
        self._max_workers = max(1, int(max_workers))
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)

        self._domain_locks: dict[str, threading.Lock] = {}
        self._domain_locks_master = threading.Lock()
        self._in_progress: set[str] = set()
        self._in_progress_lock = threading.Lock()
        self._failure_counters: dict[str, int] = {}
        self._failure_notified: set[str] = set()
        self._counters_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public helpers (test introspection)
    # ------------------------------------------------------------------

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def failure_count(self, site_id: str) -> int:
        with self._counters_lock:
            return self._failure_counters.get(site_id, 0)

    def shutdown(self, wait: bool = False) -> None:
        try:
            self._executor.shutdown(wait=wait)
        except Exception as exc:  # pragma: no cover - shutdown best-effort
            logger.debug("Watchtower executor shutdown error: %s", exc)

    # ------------------------------------------------------------------
    # Internal locking helpers
    # ------------------------------------------------------------------

    def _get_domain_lock(self, url: str) -> threading.Lock:
        domain = urlparse(url or "").netloc or "_no-domain"
        with self._domain_locks_master:
            lock = self._domain_locks.get(domain)
            if lock is None:
                lock = threading.Lock()
                self._domain_locks[domain] = lock
            return lock

    def _try_claim(self, site_id: str) -> bool:
        with self._in_progress_lock:
            if site_id in self._in_progress:
                return False
            self._in_progress.add(site_id)
            return True

    def _release(self, site_id: str) -> None:
        with self._in_progress_lock:
            self._in_progress.discard(site_id)

    # ------------------------------------------------------------------
    # Failure / notification accounting
    # ------------------------------------------------------------------

    def _record_success(self, site_id: str) -> None:
        with self._counters_lock:
            self._failure_counters.pop(site_id, None)
            self._failure_notified.discard(site_id)

    def _record_failure(self, site_id: str) -> tuple[int, bool]:
        """Bump the streak; return (count, should_notify)."""
        with self._counters_lock:
            count = self._failure_counters.get(site_id, 0) + 1
            self._failure_counters[site_id] = count
            should_notify = count >= FAILURE_THRESHOLD and site_id not in self._failure_notified
            if should_notify:
                self._failure_notified.add(site_id)
            return count, should_notify

    def _notify_owner_failure(self, site: Site, count: int) -> None:
        """Log-only owner notification (Step 4 will route to real channel)."""
        logger.error(
            "[watchtower] Site failure threshold reached: id=%s name=%s "
            "consecutive_failures=%d category=%s — owner notification deferred to Step 4 Notifier",
            site.id,
            site.name,
            count,
            site.category_id,
        )

    # ------------------------------------------------------------------
    # Single-site crawl
    # ------------------------------------------------------------------

    def run_site(self, site_id: str) -> dict[str, Any]:
        """Crawl a single site by id. Returns a result summary dict.

        Never raises. Concurrent re-entry on the same site_id returns
        ``{"site_id": ..., "skipped": "in_progress"}`` immediately.
        """
        if not self._try_claim(site_id):
            return {"site_id": site_id, "skipped": "in_progress"}

        started = time.monotonic()
        try:
            return self._run_site_locked(site_id, started)
        finally:
            self._release(site_id)

    def _run_site_locked(self, site_id: str, started: float) -> dict[str, Any]:
        # Phase 1: load the site.
        with self._session_factory() as session:
            site = session.get(Site, site_id)
            if site is None:
                return {"site_id": site_id, "error": "site not found"}
            if not site.enabled:
                return {"site_id": site_id, "skipped": "disabled"}
            site_url = site.url
            crawl_method = site.crawl_method
            site_name = site.name

        # Phase 2: robots.txt.
        try:
            allowed = robots_mod.is_allowed(
                site_url, self._user_agent, timeout_sec=self._timeout_sec
            )
        except Exception as exc:
            logger.debug("robots check raised for %s: %s", site_id, exc)
            allowed = True  # fail-open

        if not allowed:
            with self._session_factory() as session:
                site = session.get(Site, site_id)
                if site is not None:
                    site.status = "blocked"
                    session.commit()
            return {
                "site_id": site_id,
                "status": "blocked",
                "blocked_by_robots": True,
                "items_new": 0,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }

        # Phase 3: domain-locked fetch.
        domain_lock = self._get_domain_lock(site_url)
        try:
            crawler = self._crawler_factory(crawl_method)
        except ValueError as exc:
            return self._record_run_failure(site_id, site_name, str(exc), started)

        with domain_lock:
            class _StubSite:
                def __init__(self, sid: str, url: str, selector: str | None) -> None:
                    self.id = sid
                    self.url = url
                    self.content_selector = selector

            with self._session_factory() as session:
                site = session.get(Site, site_id)
                if site is None:
                    return {"site_id": site_id, "error": "site disappeared"}
                stub = _StubSite(site.id, site.url, site.content_selector)
            try:
                result: CrawlResult = crawler.crawl(
                    stub, user_agent=self._user_agent, timeout_sec=self._timeout_sec
                )
            except Exception as exc:
                logger.exception("Crawler raised for site %s", site_id)
                return self._record_run_failure(
                    site_id, site_name, f"crawler raised: {type(exc).__name__}", started
                )

        # Phase 4: persist.
        if result.error:
            return self._record_run_failure(site_id, site_name, result.error, started)

        try:
            with self._session_factory() as session:
                new_items = detect_new_items(session, site_id, result.items)
                site = session.get(Site, site_id)
                if site is not None:
                    site.status = "ok"
                    site.last_ok_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as exc:
            logger.exception("DB commit failed for site %s", site_id)
            return self._record_run_failure(
                site_id, site_name, f"db error: {type(exc).__name__}", started
            )

        self._record_success(site_id)
        return {
            "site_id": site_id,
            "status": "ok",
            "items_new": len(new_items),
            "items_seen": len(result.items),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    def _record_run_failure(
        self, site_id: str, site_name: str, error: str, started: float
    ) -> dict[str, Any]:
        count, should_notify = self._record_failure(site_id)
        try:
            with self._session_factory() as session:
                site = session.get(Site, site_id)
                if site is not None:
                    if count >= FAILURE_THRESHOLD:
                        site.status = "failed"
                    else:
                        site.status = "delayed"
                    session.commit()
                    if should_notify:
                        self._notify_owner_failure(site, count)
        except Exception as exc:  # pragma: no cover - DB write best-effort
            logger.warning("Could not persist failure status for %s: %s", site_id, exc)

        logger.warning(
            "[watchtower] crawl failed: site=%s (%s) reason=%s consecutive=%d",
            site_id, site_name, error, count,
        )
        return {
            "site_id": site_id,
            "status": "failed" if count >= FAILURE_THRESHOLD else "delayed",
            "error": error,
            "consecutive_failures": count,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }

    # ------------------------------------------------------------------
    # Fan-out
    # ------------------------------------------------------------------

    def run_all(self, *, only_enabled: bool = True) -> dict[str, Any]:
        """Submit a ``run_site`` task for every (enabled) site in parallel.

        Returns ``{"sites_run": N, "results": [...]}``. Site lookup happens
        once at the top so the executor never hits the DB during fan-out.
        """
        with self._session_factory() as session:
            stmt = select(Site.id)
            if only_enabled:
                stmt = stmt.where(Site.enabled.is_(True))
            site_ids = list(session.execute(stmt.order_by(Site.id)).scalars().all())

        results: list[dict[str, Any]] = []
        if not site_ids:
            return {"sites_run": 0, "results": results}

        futures = {
            self._executor.submit(self.run_site, sid): sid for sid in site_ids
        }
        for future in as_completed(futures):
            sid = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # pragma: no cover - run_site never raises
                logger.exception("Future for %s raised", sid)
                results.append({"site_id": sid, "error": f"{type(exc).__name__}"})
        return {"sites_run": len(site_ids), "results": results}
