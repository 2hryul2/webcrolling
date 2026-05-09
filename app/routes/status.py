"""Status / events / alerts / trigger HTTP routes."""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Query, Request

from app.database import load_jsonl

logger = logging.getLogger(__name__)

# psutil is a hard dep (added to requirements.txt). Imported here so /status
# can report process RSS / CPU as the spec requires (FR-8 + section 6).
try:
    import psutil  # type: ignore
    _PROCESS = psutil.Process()
    # Prime cpu_percent — first call returns 0.0; subsequent calls return delta.
    try:
        _PROCESS.cpu_percent(interval=None)
    except Exception:
        pass
except Exception:  # pragma: no cover
    psutil = None  # type: ignore
    _PROCESS = None

router = APIRouter()

_START_TIME = time.time()


def _get_worker(request: Request) -> Any:
    """Pull the Worker from FastAPI app state, or None if unset."""
    return getattr(request.app.state, "worker", None)


def _process_metrics() -> dict[str, Any]:
    """Best-effort process memory_mb + cpu_percent (psutil)."""
    metrics: dict[str, Any] = {"memory_mb": None, "cpu_percent": None}
    if _PROCESS is None:
        return metrics
    try:
        rss_bytes = _PROCESS.memory_info().rss
        metrics["memory_mb"] = round(rss_bytes / (1024 * 1024), 2)
    except Exception:
        pass
    try:
        metrics["cpu_percent"] = _PROCESS.cpu_percent(interval=None)
    except Exception:
        pass
    return metrics


def _count_alerts_by_status(filepath: str, status_value: str) -> int:
    """Read the entire alerts.jsonl and count rows with matching status."""
    rows = load_jsonl(filepath)
    return sum(1 for r in rows if r.get("status") == status_value)


def _count_events_by_source(rows: list[dict], source_id: str) -> int:
    return sum(1 for r in rows if r.get("source") == source_id)


def _count_alerts_by_source(rows: list[dict], events_index: dict[str, str], source_id: str) -> int:
    """Alerts log uses event_id; we need to join to events.source."""
    return sum(
        1
        for r in rows
        if events_index.get(r.get("event_id", "")) == source_id
        and r.get("status") in ("sent", "failed", None)
    )


@router.get("/status")
def get_status(request: Request) -> dict[str, Any]:
    """Return system status — uptime, last poll, counters, source health.

    Spec section 6 contract: uptime_seconds, failed_alert_count, per-source
    {status, event_count, alert_count, error_count}, memory_mb, cpu_percent.
    """
    worker = _get_worker(request)
    state: dict[str, Any] = {}
    sources: dict[str, dict[str, Any]] = {}
    failed_alert_count = 0

    if worker is not None:
        state = worker.get_state() or {}
        # Aggregate per-source counts from JSONL (small N — Step 1 is fine).
        events = load_jsonl(worker.events_path)
        alerts = load_jsonl(worker.alerts_path)
        events_index = {e.get("external_id", ""): e.get("source", "") for e in events}
        failed_alert_count = sum(1 for a in alerts if a.get("status") == "failed")
        error_counts = (
            worker.get_error_counts()
            if hasattr(worker, "get_error_counts")
            else {}
        )

        for collector in getattr(worker, "collectors", []) or []:
            sid = collector.source_id
            last = (state.get("last_poll", {}) or {}).get(sid)
            err_count = int(error_counts.get(sid, 0))
            sources[sid] = {
                "name": collector.name,
                "endpoint": collector.endpoint,
                "last_poll": last,
                "status": "error" if err_count > 0 else "ok",
                "event_count": _count_events_by_source(events, sid),
                "alert_count": _count_alerts_by_source(alerts, events_index, sid),
                "error_count": err_count,
            }

    uptime_seconds = int(time.time() - _START_TIME)
    metrics = _process_metrics()
    return {
        "status": "ok",
        "now": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": uptime_seconds,
        "last_poll": state.get("last_poll", {}),
        "event_count": int(state.get("event_count", 0) or 0),
        "alert_count": int(state.get("alert_count", 0) or 0),
        "failed_alert_count": int(failed_alert_count),
        "sources": sources,
        "memory_mb": metrics["memory_mb"],
        "cpu_percent": metrics["cpu_percent"],
    }


def _within_days(record_dt_field: Any, days: int) -> bool:
    """True if `record_dt_field` (ISO 8601 string) is within last `days` days."""
    if not record_dt_field:
        return False
    try:
        dt = datetime.fromisoformat(str(record_dt_field).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return dt >= cutoff


@router.get("/events")
def get_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    source: Optional[str] = Query(default=None),
    days: Optional[int] = Query(default=None, ge=1, le=3650),
) -> dict[str, Any]:
    """Return recent events from data/events.jsonl with optional filters."""
    worker = _get_worker(request)
    if worker is None:
        return {"limit": limit, "count": 0, "events": []}

    # Load up to a generous cap, then filter — Step 1 file is small.
    rows = load_jsonl(worker.events_path)
    if source:
        rows = [r for r in rows if r.get("source") == source]
    if days:
        rows = [r for r in rows if _within_days(r.get("published_at") or r.get("fetched_at"), days)]
    rows = rows[-limit:]
    return {"limit": limit, "count": len(rows), "events": rows}


@router.get("/alerts")
def get_alerts(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    severity: Optional[str] = Query(default=None),
    days: Optional[int] = Query(default=None, ge=1, le=3650),
) -> dict[str, Any]:
    """Return recent alerts from data/alerts.jsonl with optional filters.

    `severity` filtering crosses to events.jsonl since alerts.jsonl carries
    only event_id; we resolve severity via the events index.
    """
    worker = _get_worker(request)
    if worker is None:
        return {"limit": limit, "count": 0, "alerts": []}

    rows = load_jsonl(worker.alerts_path)
    if severity:
        events = load_jsonl(worker.events_path)
        sev_index = {e.get("external_id", ""): e.get("severity", "") for e in events}
        rows = [r for r in rows if sev_index.get(r.get("event_id", "")) == severity]
    if days:
        rows = [r for r in rows if _within_days(r.get("sent_at"), days)]
    rows = rows[-limit:]
    return {"limit": limit, "count": len(rows), "alerts": rows}


def _trigger_run(worker: Any, source_id: Optional[str]) -> None:
    """Background-task wrapper around worker.run_once — never raises."""
    try:
        worker.run_once(source_id=source_id)
    except Exception as exc:
        logger.warning("Background trigger failed: %s", type(exc).__name__)


@router.post("/trigger", status_code=202)
def trigger(
    request: Request,
    background_tasks: BackgroundTasks,
    source: Optional[str] = Query(default=None),
) -> dict[str, Any]:
    """Queue a single collection pass and return 202 Accepted.

    Spec section 6 contract: 202 Accepted with {job_id, source, status, message}.
    The actual run happens off the event loop via FastAPI BackgroundTasks.
    """
    worker = _get_worker(request)
    job_id = str(uuid.uuid4())
    if worker is None:
        return {
            "job_id": job_id,
            "source": source or "all",
            "status": "queued",
            "message": "Trigger accepted (no worker — will be a no-op)",
        }
    background_tasks.add_task(_trigger_run, worker, source)
    return {
        "job_id": job_id,
        "source": source or "all",
        "status": "queued",
        "message": "Trigger accepted",
    }
