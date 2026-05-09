"""Status / events / alerts / trigger HTTP routes."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request

from app.database import load_jsonl, load_state

logger = logging.getLogger(__name__)

router = APIRouter()

_START_TIME = time.time()


def _get_worker(request: Request) -> Any:
    """Pull the Worker from FastAPI app state, or None if unset."""
    return getattr(request.app.state, "worker", None)


@router.get("/status")
def get_status(request: Request) -> dict[str, Any]:
    """Return system status — uptime, last poll, counters, source health."""
    worker = _get_worker(request)
    state: dict[str, Any] = {}
    sources: dict[str, dict[str, Any]] = {}
    if worker is not None:
        state = worker.get_state() or {}
        for collector in getattr(worker, "collectors", []) or []:
            last = (state.get("last_poll", {}) or {}).get(collector.source_id)
            sources[collector.source_id] = {
                "name": collector.name,
                "endpoint": collector.endpoint,
                "last_poll": last,
            }

    uptime_sec = int(time.time() - _START_TIME)
    return {
        "status": "ok",
        "now": datetime.now(timezone.utc).isoformat(),
        "uptime_sec": uptime_sec,
        "last_poll": state.get("last_poll", {}),
        "event_count": int(state.get("event_count", 0) or 0),
        "alert_count": int(state.get("alert_count", 0) or 0),
        "sources": sources,
    }


@router.get("/events")
def get_events(request: Request, limit: int = 100) -> dict[str, Any]:
    """Return recent events from data/events.jsonl."""
    worker = _get_worker(request)
    if worker is None:
        return {"limit": limit, "count": 0, "events": []}
    events = load_jsonl(worker.events_path, limit=max(1, min(int(limit), 1000)))
    return {"limit": limit, "count": len(events), "events": events}


@router.get("/alerts")
def get_alerts(request: Request, limit: int = 100) -> dict[str, Any]:
    """Return recent alerts from data/alerts.jsonl."""
    worker = _get_worker(request)
    if worker is None:
        return {"limit": limit, "count": 0, "alerts": []}
    alerts = load_jsonl(worker.alerts_path, limit=max(1, min(int(limit), 1000)))
    return {"limit": limit, "count": len(alerts), "alerts": alerts}


@router.post("/trigger")
def trigger(request: Request) -> dict[str, Any]:
    """Manually trigger a single collection pass."""
    worker = _get_worker(request)
    if worker is None:
        return {"status": "no-worker", "new_events": 0}
    try:
        new_count = worker.run_once()
        return {"status": "ok", "new_events": int(new_count)}
    except Exception as exc:
        logger.warning("trigger failed: %s", exc)
        return {"status": "error", "error": str(exc), "new_events": 0}
