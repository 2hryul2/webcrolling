"""Worker — orchestrates collection, dedup, matching, notification."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from app.database import (
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
            endpoint = cfg.get("endpoint") or ""
            if not endpoint:
                continue
            if source_id == "dart":
                collectors.append(DARTCollector(name=name, endpoint=endpoint))
            elif source_id == "fsc":
                collectors.append(FSCCollector(name=name, endpoint=endpoint))
            else:
                collectors.append(RSSCollector(source_id=source_id, name=name, endpoint=endpoint))
        return collectors

    def run_once(self, source_id: Optional[str] = None) -> int:
        """Run a single collection pass.

        If source_id is given, only that source runs. Returns total new events.
        """
        total_new = 0
        state = load_state(self.state_path)
        last_poll = state.get("last_poll", {}) or {}
        event_count = int(state.get("event_count", 0) or 0)
        alert_count = int(state.get("alert_count", 0) or 0)

        for collector in self.collectors:
            if source_id and collector.source_id != source_id:
                continue
            try:
                events = collector.collect()
                logger.info("Collected %d events from %s", len(events), collector.source_id)
            except Exception as exc:
                logger.warning("Collector %s failed: %s", collector.source_id, exc)
                events = []

            for event in events:
                if event.content_hash in self.dedup_cache:
                    continue
                # Match
                haystack = " ".join(filter(None, [event.title, event.summary or ""]))
                severity, matched = self.matcher.match(haystack)
                event = event.model_copy(
                    update={"severity": severity, "matched_keywords": matched or None}
                )
                # Persist event
                if append_jsonl(self.events_path, event):
                    self.dedup_cache.add(event.content_hash)
                    total_new += 1
                    event_count += 1
                # Notify
                try:
                    self.notifier.notify(event)
                    alert_count += 1
                except Exception as exc:
                    logger.warning("Notify failed for %s: %s", event.external_id, exc)

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
