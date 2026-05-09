"""Pydantic data models for the web monitoring system."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_serializer


def _json_default(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def _parse_dt(value: Any) -> Any:
    """Parse ISO datetime strings back to datetime."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


class ExternalEvent(BaseModel):
    """Normalized external event collected from RSS/API sources."""

    source: str
    external_id: str
    title: str
    url: str
    published_at: datetime
    fetched_at: datetime
    summary: Optional[str] = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    content_hash: str
    severity: Literal["urgent", "watch", "info"] = "info"
    matched_keywords: list[str] = Field(default_factory=list)
    is_duplicate: bool = False

    @field_serializer("published_at", "fetched_at")
    def serialize_dt(self, dt: datetime) -> str:
        return dt.isoformat()

    def to_jsonl(self) -> str:
        """Serialize to a single-line JSON string for JSONL append."""
        data = self.model_dump()
        return json.dumps(data, default=_json_default, ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> "ExternalEvent":
        """Deserialize from one JSONL line."""
        raw = json.loads(line)
        if "published_at" in raw:
            raw["published_at"] = _parse_dt(raw["published_at"])
        if "fetched_at" in raw:
            raw["fetched_at"] = _parse_dt(raw["fetched_at"])
        return cls(**raw)


class AlertLog(BaseModel):
    """Record of an alert that was sent (or attempted)."""

    event_id: str
    channel: Literal["email", "file"]
    recipient: str
    sent_at: datetime
    status: Literal["sent", "failed"]
    error_message: Optional[str] = None

    @field_serializer("sent_at")
    def serialize_sent_at(self, dt: datetime) -> str:
        return dt.isoformat()

    def to_jsonl(self) -> str:
        """Serialize to a single-line JSON string for JSONL append."""
        data = self.model_dump()
        return json.dumps(data, default=_json_default, ensure_ascii=False)

    @classmethod
    def from_jsonl(cls, line: str) -> "AlertLog":
        """Deserialize from one JSONL line."""
        raw = json.loads(line)
        if "sent_at" in raw:
            raw["sent_at"] = _parse_dt(raw["sent_at"])
        return cls(**raw)


class KeywordRule(BaseModel):
    """Single keyword rule with optional exclusion list."""

    keyword: str
    severity: Literal["urgent", "watch", "info"]
    # Step 2 scaffolding: matcher uses this in Step 2 for fuzzy/exclusion matching.
    exclude_keywords: Optional[list[str]] = None


class SystemState(BaseModel):
    """Persisted system state — last poll times and counters."""

    last_poll: dict[str, datetime] = Field(default_factory=dict)
    event_count: int = 0
    alert_count: int = 0

    @field_serializer("last_poll")
    def serialize_last_poll(self, value: dict[str, datetime]) -> dict[str, str]:
        return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in value.items()}
