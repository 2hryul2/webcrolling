"""Worker — orchestrates collection, dedup, matching, notification."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from app.database import (
    append_if_new,
    append_jsonl,
    load_existing_hashes,
    load_state,
    save_state,
)
from app.models import ExternalEvent
from monitor.collectors.dart import DARTCollector
from monitor.collectors.fsc import FSCCollector
from monitor.collectors.rss import RSSCollector
from monitor.matcher import KeywordMatcher
from monitor.notifier import Notifier

logger = logging.getLogger(__name__)


def _resolve_thread_pool_size() -> int:
    """Read THREAD_POOL_SIZE env var; default 5, hard cap 32 (NFR-10)."""
    try:
        n = int(os.getenv("THREAD_POOL_SIZE", "5"))
    except (TypeError, ValueError):
        n = 5
    return max(1, min(n, 32))


class Worker:
    """Top-level orchestration for a single collection cycle."""

    def __init__(
        self,
        config_path: str,
        keywords_path: str,
        data_dir: str,
        smtp_config: Optional[dict] = None,
    ) -> None:
        self.config_path = config_path
        self.keywords_path = keywords_path
        self.data_dir = data_dir
        Path(data_dir).mkdir(parents=True, exist_ok=True)

        self.events_path = os.path.join(data_dir, "events.jsonl")
        self.alerts_path = os.path.join(data_dir, "alerts.jsonl")
        self.state_path = os.path.join(data_dir, "state.json")

        self.sources_config = self._load_sources()
        self.matcher = KeywordMatcher(keywords_path)
        self.notifier = Notifier(smtp_config or {}, self.alerts_path)

        self.collectors: list[RSSCollector] = self._build_collectors()
        self.dedup_cache: set = load_existing_hashes(self.events_path)
        self.error_counts: dict[str, int] = {c.source_id: 0 for c in self.collectors}
        logger.info(
            "Worker initialized — %d collectors, %d known hashes",
            len(self.collectors),
            len(self.dedup_cache),
        )

    def _load_sources(self) -> dict:
        path = Path(self.config_path)
        if not path.exists():
            logger.warning("sources config missing: %s", path)
            return {"sources": {}}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {"sources": {}}

    def _build_collectors(self) -> list[RSSCollector]:
        collectors: list[RSSCollector] = []
        sources = (self.sources_config or {}).get("sources", {}) or {}
        for source_id, cfg in sources.items():
            if not cfg.get("enabled", False):
                continue
            name = cfg.get("name") or source_id
            url = cfg.get("url") or cfg.get("endpoint") or ""
            if not url:
                continue
            timeout = int(cfg.get("timeout_seconds", 30))
            retry = int(cfg.get("retry_attempts", 3))
            if source_id == "dart":
                collectors.append(
                    DARTCollector(
                        name=name,
                        endpoint=url,
                        timeout_seconds=timeout,
                        retry_attempts=retry,
                    )
                )
            elif source_id == "fsc":
                collectors.append(
                    FSCCollector(
                        name=name,
                        endpoint=url,
                        timeout_seconds=timeout,
                        retry_attempts=retry,
                    )
                )
            else:
                collectors.append(
                    RSSCollector(
                        source_id=source_id,
                        name=name,
                        endpoint=url,
                        timeout_seconds=timeout,
                        retry_attempts=retry,
                    )
                )
        return collectors

    def run_once(self, source_id: Optional[str] = None) -> int:
        """Run a single collection pass.

        Collectors run in parallel via ThreadPoolExecutor (NFR-10), but events
        are processed sequentially after gathering — single-threaded JSONL
        writes are safer and dedup logic stays simple. Returns total new events.
        """
        total_new = 0
        state = load_state(self.state_path)
        last_poll = state.get("last_poll", {}) or {}
        event_count = int(state.get("event_count", 0) or 0)
        alert_count = int(state.get("alert_count", 0) or 0)

        active_collectors = [
            c for c in self.collectors if (source_id is None or c.source_id == source_id)
        ]
        if not active_collectors:
            return 0

        max_workers = min(_resolve_thread_pool_size(), len(active_collectors))
        results: dict[str, list[ExternalEvent]] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_id = {
                executor.submit(c.collect): c.source_id for c in active_collectors
            }
            for future in as_completed(future_to_id):
                sid = future_to_id[future]
                try:
                    events = future.result() or []
                    results[sid] = events
                    logger.info("Collected %d events from %s", len(events), sid)
                except Exception as exc:
                    logger.warning("Collector %s failed: %s", sid, type(exc).__name__)
                    self.error_counts[sid] = self.error_counts.get(sid, 0) + 1
                    results[sid] = []

        for collector in active_collectors:
            events = results.get(collector.source_id, [])
            for event in events:
                # Match (still attempt, even on duplicate, so log accurately reflects severity)
                haystack = " ".join(filter(None, [event.title, event.summary or ""]))
                severity, matched = self.matcher.match(haystack)

                if event.content_hash in self.dedup_cache:
                    # Duplicate path: write with is_duplicate=True, skip notification.
                    dup_event = event.model_copy(
                        update={
                            "severity": severity,
                            "matched_keywords": matched,
                            "is_duplicate": True,
                        }
                    )
                    append_jsonl(self.events_path, dup_event)
                    logger.info("Duplicate detected: %s", event.title)
                    continue

                fresh_event = event.model_copy(
                    update={
                        "severity": severity,
                        "matched_keywords": matched,
                        "is_duplicate": False,
                    }
                )
                # Atomic check-and-insert under per-file lock (FR-4 / dedup race).
                inserted = append_if_new(
                    self.events_path,
                    fresh_event,
                    self.dedup_cache,
                    fresh_event.content_hash,
                )
                if not inserted:
                    # Lost the race to another writer — treat as duplicate.
                    dup_event = fresh_event.model_copy(update={"is_duplicate": True})
                    append_jsonl(self.events_path, dup_event)
                    logger.info("Duplicate detected (race): %s", event.title)
                    continue

                total_new += 1
                event_count += 1
                # Notify
                try:
                    self.notifier.notify(fresh_event)
                    alert_count += 1
                except Exception as exc:
                    logger.warning(
                        "Notify failed for %s: %s",
                        fresh_event.external_id,
                        type(exc).__name__,
                    )

            last_poll[collector.source_id] = datetime.now(timezone.utc).isoformat()

        new_state = {
            "last_poll": last_poll,
            "event_count": event_count,
            "alert_count": alert_count,
        }
        save_state(self.state_path, new_state)
        return total_new

    def get_state(self) -> dict:
        return load_state(self.state_path)

    def get_error_counts(self) -> dict[str, int]:
        return dict(self.error_counts)
