"""HTTP route tests — query validation, filters, /trigger 202, /status shape."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import append_jsonl, compute_content_hash
from app.models import AlertLog, ExternalEvent
from app.routes.status import router as status_router


class _FakeCollector:
    def __init__(self, source_id: str, name: str = "fake", endpoint: str = "https://x"):
        self.source_id = source_id
        self.name = name
        self.endpoint = endpoint


class _FakeWorker:
    def __init__(self, data_dir: Path):
        self.data_dir = str(data_dir)
        self.events_path = str(data_dir / "events.jsonl")
        self.alerts_path = str(data_dir / "alerts.jsonl")
        self.state_path = str(data_dir / "state.json")
        self.collectors = [
            _FakeCollector("dart", "DART"),
            _FakeCollector("fsc", "FSC"),
        ]
        self.error_counts = {"dart": 0, "fsc": 0}
        self._state = {
            "last_poll": {"dart": "2026-05-09T12:00:00+00:00"},
            "event_count": 0,
            "alert_count": 0,
        }
        self.run_once_calls: list[Any] = []

    def get_state(self) -> dict:
        return dict(self._state)

    def get_error_counts(self) -> dict:
        return dict(self.error_counts)

    def run_once(self, source_id=None):
        self.run_once_calls.append(source_id)
        return 0


@pytest.fixture
def client_with_data(tmp_data_dir):
    """FastAPI app + TestClient with a populated data directory."""
    # Seed events.jsonl
    e_dart_urgent = ExternalEvent(
        source="dart",
        external_id="dart-1",
        title="구조조정 발표",
        url="https://dart.example.com/1",
        published_at=datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 9, 10, 5, tzinfo=timezone.utc),
        summary=None,
        raw_payload={},
        content_hash=compute_content_hash("구조조정 발표", "https://dart.example.com/1"),
        severity="urgent",
        matched_keywords=["구조조정"],
    )
    e_fsc_info = ExternalEvent(
        source="fsc",
        external_id="fsc-1",
        title="공시 안내",
        url="https://fsc.example.com/1",
        published_at=datetime(2026, 5, 9, 10, 10, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 9, 10, 15, tzinfo=timezone.utc),
        summary=None,
        raw_payload={},
        content_hash=compute_content_hash("공시 안내", "https://fsc.example.com/1"),
        severity="info",
        matched_keywords=[],
    )
    events_path = tmp_data_dir / "events.jsonl"
    alerts_path = tmp_data_dir / "alerts.jsonl"
    append_jsonl(str(events_path), e_dart_urgent)
    append_jsonl(str(events_path), e_fsc_info)

    a_sent = AlertLog(
        event_id="dart-1",
        channel="email",
        recipient="ops@example.com",
        sent_at=datetime(2026, 5, 9, 10, 6, tzinfo=timezone.utc),
        status="sent",
    )
    a_failed = AlertLog(
        event_id="dart-1",
        channel="email",
        recipient="ops@example.com",
        sent_at=datetime(2026, 5, 9, 10, 7, tzinfo=timezone.utc),
        status="failed",
        error_message="connection refused",
    )
    a_info = AlertLog(
        event_id="fsc-1",
        channel="file",
        recipient=str(alerts_path),
        sent_at=datetime(2026, 5, 9, 10, 16, tzinfo=timezone.utc),
        status="sent",
    )
    append_jsonl(str(alerts_path), a_sent)
    append_jsonl(str(alerts_path), a_failed)
    append_jsonl(str(alerts_path), a_info)

    app = FastAPI()
    app.include_router(status_router)
    fake_worker = _FakeWorker(tmp_data_dir)
    app.state.worker = fake_worker
    return TestClient(app), fake_worker


# ---------- /events ----------


def test_events_limit_2000_returns_422(client_with_data):
    """C5 — out-of-range limit must trigger FastAPI's validation 422."""
    client, _ = client_with_data
    resp = client.get("/events?limit=2000")
    assert resp.status_code == 422


def test_events_limit_zero_returns_422(client_with_data):
    client, _ = client_with_data
    resp = client.get("/events?limit=0")
    assert resp.status_code == 422


def test_events_filter_by_source(client_with_data):
    """C5 — `source` query param filters in-memory."""
    client, _ = client_with_data
    resp = client.get("/events?source=fsc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["events"][0]["source"] == "fsc"


def test_events_default_limit_returns_all(client_with_data):
    client, _ = client_with_data
    resp = client.get("/events")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2


# ---------- /alerts ----------


def test_alerts_filter_by_severity(client_with_data):
    """C5 — `severity` filter joins alerts to events.severity."""
    client, _ = client_with_data
    resp = client.get("/alerts?severity=urgent")
    assert resp.status_code == 200
    body = resp.json()
    # Two alerts reference dart-1 (urgent)
    assert body["count"] == 2
    assert all(a["event_id"] == "dart-1" for a in body["alerts"])


def test_alerts_default_returns_all(client_with_data):
    client, _ = client_with_data
    resp = client.get("/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3


def test_alerts_limit_invalid_returns_422(client_with_data):
    client, _ = client_with_data
    resp = client.get("/alerts?limit=-1")
    assert resp.status_code == 422


# ---------- /trigger ----------


def test_trigger_returns_202_with_job_id(client_with_data):
    """C6 — POST /trigger returns 202 + {job_id, source, status, message}."""
    client, fake_worker = client_with_data
    resp = client.post("/trigger")
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body and len(body["job_id"]) > 0
    assert body["source"] == "all"
    assert body["status"] == "queued"
    assert body["message"]
    # BackgroundTasks fire after response — TestClient processes them sync.
    assert fake_worker.run_once_calls == [None]


def test_trigger_with_source_param(client_with_data):
    client, fake_worker = client_with_data
    resp = client.post("/trigger?source=dart")
    assert resp.status_code == 202
    body = resp.json()
    assert body["source"] == "dart"
    assert fake_worker.run_once_calls == ["dart"]


# ---------- /status ----------


def test_status_response_shape(client_with_data):
    """C7 — /status response contains all spec-mandated fields."""
    client, _ = client_with_data
    resp = client.get("/status")
    assert resp.status_code == 200
    body = resp.json()

    # Required top-level fields
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
    assert isinstance(body["uptime_seconds"], int)
    assert "event_count" in body
    assert "alert_count" in body
    assert "failed_alert_count" in body
    assert body["failed_alert_count"] == 1  # one failed alert seeded
    assert "memory_mb" in body
    assert "cpu_percent" in body

    # Per-source structure
    sources = body["sources"]
    for sid in ("dart", "fsc"):
        assert sid in sources
        s = sources[sid]
        assert "status" in s and s["status"] in ("ok", "error")
        assert "event_count" in s
        assert "alert_count" in s
        assert "error_count" in s

    # Counts derived from the seeded JSONL files
    assert sources["dart"]["event_count"] == 1
    assert sources["fsc"]["event_count"] == 1
    assert sources["dart"]["alert_count"] == 2  # 2 alerts pointing at dart-1
    assert sources["fsc"]["alert_count"] == 1


def test_status_sources_status_ok_when_no_errors(client_with_data):
    client, fake_worker = client_with_data
    fake_worker.error_counts = {"dart": 0, "fsc": 0}
    resp = client.get("/status")
    body = resp.json()
    assert body["sources"]["dart"]["status"] == "ok"


def test_status_sources_status_error_when_errors(client_with_data):
    client, fake_worker = client_with_data
    fake_worker.error_counts = {"dart": 3, "fsc": 0}
    resp = client.get("/status")
    body = resp.json()
    assert body["sources"]["dart"]["status"] == "error"
    assert body["sources"]["dart"]["error_count"] == 3
    assert body["sources"]["fsc"]["status"] == "ok"
