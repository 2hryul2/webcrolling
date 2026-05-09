"""Tests for monitor/notifier.py — STARTTLS, retries, redaction, graceful skip."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.database import compute_content_hash, load_jsonl
from app.models import ExternalEvent
from monitor.notifier import Notifier, _redact_password_substrings


def _make_event(severity: str = "urgent") -> ExternalEvent:
    return ExternalEvent(
        source="dart",
        external_id="evt-1",
        title="Test 구조조정",
        url="https://example.com/1",
        published_at=datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
        fetched_at=datetime(2026, 5, 9, 10, 5, tzinfo=timezone.utc),
        summary="요약",
        raw_payload={},
        content_hash=compute_content_hash("Test 구조조정", "https://example.com/1"),
        severity=severity,  # type: ignore[arg-type]
        matched_keywords=["구조조정"],
    )


def _smtp_config() -> dict:
    return {
        "server": "smtp.example.com",
        "port": 587,
        "user": "alerts@example.com",
        "password": "supersecret",
        "alert_email": "ops@example.com",
    }


@pytest.fixture
def alerts_path(tmp_data_dir):
    return str(tmp_data_dir / "alerts.jsonl")


# ---------- STARTTLS fail-closed ----------


def test_starttls_failure_does_not_login_plaintext(alerts_path, monkeypatch):
    """C10 — STARTTLS exception → no smtp.login call, AlertLog status=failed."""
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.__exit__.return_value = False
    smtp.starttls.side_effect = RuntimeError("upgrade rejected")

    monkeypatch.setattr("monitor.notifier.smtplib.SMTP", lambda *a, **k: smtp)
    monkeypatch.setattr("monitor.notifier.time.sleep", lambda s: None)

    notifier = Notifier(_smtp_config(), alerts_path)
    log = notifier._send_email(_make_event("urgent"))

    assert log.status == "failed"
    assert log.error_message == "STARTTLS failed"
    smtp.login.assert_not_called()
    smtp.send_message.assert_not_called()


# ---------- Retry-then-fail ----------


def test_email_retry_then_fail_three_attempts(alerts_path, monkeypatch):
    """C2 — three SMTP attempts on transient errors, then failed AlertLog."""
    call_count = {"n": 0}

    class _BoomSMTP:
        def __init__(self, *args, **kwargs):
            call_count["n"] += 1
            self.timeout = kwargs.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def send_message(self, msg):
            raise ConnectionError("smtp down")

    monkeypatch.setattr("monitor.notifier.smtplib.SMTP", _BoomSMTP)
    monkeypatch.setattr("monitor.notifier.time.sleep", lambda s: None)

    notifier = Notifier(_smtp_config(), alerts_path)
    log = notifier._send_email(_make_event("urgent"))

    assert call_count["n"] == 3
    assert log.status == "failed"
    assert log.error_message  # carries some message


def test_smtp_timeout_is_ten_seconds(alerts_path, monkeypatch):
    """C2 — verify NFR-4: 10s timeout per attempt."""
    captured: dict = {}

    class _SMTP:
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, *a, **k):
            pass

        def send_message(self, *a, **k):
            return None

    monkeypatch.setattr("monitor.notifier.smtplib.SMTP", _SMTP)
    monkeypatch.setattr("monitor.notifier.time.sleep", lambda s: None)

    notifier = Notifier(_smtp_config(), alerts_path)
    log = notifier._send_email(_make_event("urgent"))
    assert captured["timeout"] == 10
    assert log.status == "sent"


# ---------- Credential redaction ----------


def test_email_log_redacts_credentials(alerts_path, monkeypatch):
    """C11 — error_message must not contain raw 'password=...' substrings."""
    class _SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, *a, **k):
            pass

        def send_message(self, *a, **k):
            raise RuntimeError("auth failed for password=supersecret server response")

    monkeypatch.setattr("monitor.notifier.smtplib.SMTP", _SMTP)
    monkeypatch.setattr("monitor.notifier.time.sleep", lambda s: None)

    notifier = Notifier(_smtp_config(), alerts_path)
    log = notifier._send_email(_make_event("urgent"))
    assert log.status == "failed"
    assert log.error_message is not None
    # The actual credential is gone — only the redaction marker remains.
    assert "supersecret" not in log.error_message
    assert "[REDACTED]" in log.error_message


def test_redact_password_substrings_helper():
    assert _redact_password_substrings("password=hunter2 trail") == "password=[REDACTED] trail"
    assert _redact_password_substrings('PASSWORD="my pass"') == 'PASSWORD=[REDACTED]'
    assert _redact_password_substrings("nothing here") == "nothing here"
    assert _redact_password_substrings(None) is None


# ---------- Missing config graceful skip ----------


def test_smtp_missing_config_graceful_skip(alerts_path, monkeypatch):
    """T1 — empty SMTP env, file log still written, status=failed, no crash."""
    notifier = Notifier({}, alerts_path)
    log = notifier._send_email(_make_event("urgent"))
    assert log.status == "failed"
    assert log.error_message == "SMTP not configured"

    rows = load_jsonl(alerts_path)
    assert len(rows) == 1
    assert rows[0]["error_message"] == "SMTP not configured"


def test_notify_routes_email_for_urgent(alerts_path, monkeypatch):
    """notify() must call _send_email for urgent + always file-log."""
    notifier = Notifier(_smtp_config(), alerts_path)
    sentinel = MagicMock(spec=notifier._send_email)
    sentinel.return_value = MagicMock(status="sent")
    notifier._send_email = sentinel  # type: ignore[assignment]

    notifier.notify(_make_event("urgent"))
    sentinel.assert_called_once()


def test_notify_skips_email_for_info(alerts_path, monkeypatch):
    """info-severity events bypass email entirely."""
    notifier = Notifier(_smtp_config(), alerts_path)
    sentinel = MagicMock()
    notifier._send_email = sentinel  # type: ignore[assignment]
    notifier.notify(_make_event("info"))
    sentinel.assert_not_called()


def test_alerts_jsonl_records_failed_alertlog(alerts_path, monkeypatch):
    """After retry exhaustion, AlertLog appended to JSONL."""
    monkeypatch.setattr("monitor.notifier.time.sleep", lambda s: None)

    class _SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, *a, **k):
            pass

        def send_message(self, *a, **k):
            raise OSError("network")

    monkeypatch.setattr("monitor.notifier.smtplib.SMTP", _SMTP)

    notifier = Notifier(_smtp_config(), alerts_path)
    notifier._send_email(_make_event("urgent"))

    rows = load_jsonl(alerts_path)
    assert len(rows) == 1
    rec = rows[0]
    assert rec["status"] == "failed"
    # JSON round-trip ok
    assert json.dumps(rec)
