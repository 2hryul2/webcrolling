"""Tests for Pydantic models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models import AlertLog, ExternalEvent, KeywordRule, SystemState


def test_external_event_valid_creation(sample_event_dict):
    event = ExternalEvent(**sample_event_dict)
    assert event.source == "dart"
    assert event.severity == "info"
    assert event.title.startswith("Sample")


def test_external_event_invalid_severity_raises(sample_event_dict):
    bad = dict(sample_event_dict)
    bad["severity"] = "danger"
    with pytest.raises(ValidationError):
        ExternalEvent(**bad)


def test_external_event_jsonl_round_trip(sample_event_dict):
    event = ExternalEvent(**sample_event_dict)
    line = event.to_jsonl()
    restored = ExternalEvent.from_jsonl(line)
    assert restored.source == event.source
    assert restored.external_id == event.external_id
    assert restored.title == event.title
    assert restored.published_at == event.published_at
    assert restored.fetched_at == event.fetched_at
    assert restored.content_hash == event.content_hash
    assert restored.severity == event.severity


def test_alert_log_valid_creation_and_round_trip():
    log = AlertLog(
        event_id="e-1",
        channel="email",
        recipient="x@y.com",
        sent_at=datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc),
        status="sent",
    )
    line = log.to_jsonl()
    restored = AlertLog.from_jsonl(line)
    assert restored.event_id == "e-1"
    assert restored.channel == "email"
    assert restored.status == "sent"
    assert restored.sent_at == log.sent_at


def test_alert_log_invalid_channel_raises():
    with pytest.raises(ValidationError):
        AlertLog(
            event_id="e-1",
            channel="sms",  # not allowed
            recipient="x@y.com",
            sent_at=datetime.now(timezone.utc),
            status="sent",
        )


def test_keyword_rule_basic():
    rule = KeywordRule(keyword="구조조정", severity="urgent")
    assert rule.keyword == "구조조정"
    assert rule.severity == "urgent"
    assert rule.exclude_keywords is None


def test_keyword_rule_invalid_severity():
    with pytest.raises(ValidationError):
        KeywordRule(keyword="x", severity="critical")


def test_system_state_defaults():
    s = SystemState()
    assert s.event_count == 0
    assert s.alert_count == 0
    assert s.last_poll == {}
