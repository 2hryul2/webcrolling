"""Watchtower SQLAlchemy ORM models.

Phase 1 schema covers four entities:

- Category — top-level subscription bucket (e.g. 금융 규제·감독).
- Site — a single source feed/page belonging to one Category.
- Item — a NEW/CHANGE detection on a Site (FR-FEED-* targets).
- User — single-user MVP (ASM-005). Phase 1 has exactly one row.

Decisions enforced here:

- FR-SITE-003 — `Site.crawl_interval_min` is clamped to ≥60 by `__init__`
  (validator). Lower values are accepted but emit a warning log.
- `Item.read_by` is a CSV string of user IDs (Decision §4 — avoids SQLite
  JSON1 dependency). Helpers `read_by_set()` / `mark_read(uid)` keep the
  CSV manipulation in one place.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    validates,
)

logger = logging.getLogger(__name__)

# FR-SITE-003 — minimum crawl interval, in minutes.
MIN_CRAWL_INTERVAL_MIN = 60


class Base(DeclarativeBase):
    """Common declarative base for all Watchtower tables."""


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    dept: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    messenger_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="member")  # member|owner|operator


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    owner_dept: Mapped[str] = mapped_column(String(100))
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("users.id"), nullable=True
    )

    sites: Mapped[list["Site"]] = relationship(
        back_populates="category", cascade="all, delete-orphan"
    )


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(500))
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    crawl_method: Mapped[str] = mapped_column(String(8))  # 'rss'|'html'|'js'
    content_selector: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    crawl_interval_min: Mapped[int] = mapped_column(default=MIN_CRAWL_INTERVAL_MIN)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|delayed|failed|blocked
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_ok_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    category: Mapped["Category"] = relationship(back_populates="sites")
    items: Mapped[list["Item"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )

    @validates("crawl_interval_min")
    def _clamp_crawl_interval(self, _key: str, value: int) -> int:
        """FR-SITE-003 — clamp `crawl_interval_min` to a minimum of 60."""
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = MIN_CRAWL_INTERVAL_MIN
        if v < MIN_CRAWL_INTERVAL_MIN:
            logger.warning(
                "Site.crawl_interval_min=%s clamped to %s (FR-SITE-003)",
                v, MIN_CRAWL_INTERVAL_MIN,
            )
            v = MIN_CRAWL_INTERVAL_MIN
        return v


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("site_id", "url", name="uq_site_url"),)

    id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        default=lambda: uuid.uuid4().hex,
    )
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"))
    type: Mapped[str] = mapped_column(String(8), default="NEW")  # NEW|CHANGE
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)
    url: Mapped[str] = mapped_column(String(500))
    content_hash: Mapped[str] = mapped_column(String(64))  # SHA-256 hex
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    read_by: Mapped[str] = mapped_column(String(500), default="")  # CSV of user_ids

    site: Mapped["Site"] = relationship(back_populates="items")

    # ---- read_by CSV helpers (Decision §4) -----------------------------------

    def read_by_set(self) -> set[str]:
        """Return the read_by CSV as a `set[str]`."""
        if not self.read_by:
            return set()
        return {tok for tok in (t.strip() for t in self.read_by.split(",")) if tok}

    def is_read_by(self, user_id: str) -> bool:
        return user_id in self.read_by_set()

    def mark_read(self, user_id: str) -> bool:
        """Add `user_id` to read_by. Returns True if a change was made."""
        if not user_id:
            return False
        existing = self.read_by_set()
        if user_id in existing:
            return False
        existing.add(user_id)
        # Stable order = sorted; small N, cheap, predictable in tests.
        self.read_by = ",".join(sorted(existing))
        return True


# ---------------------------------------------------------------------------
# Step 4 — Subscription + AlertLog
# ---------------------------------------------------------------------------


class Subscription(Base):
    """User × Category subscription state (FR-SUB-001).

    Cardinality: at most one row per (user_id, category_id). Decoupled
    flags keep the matrix expressive:

    - ``subscribed=False``                    → not subscribed (sidebar 기타)
    - ``subscribed=True``  + channel='off'    → ⭐ on, 🔔 off
    - ``subscribed=True``  + channel='instant'→ ⭐ + 🔔 (즉시 알림)
    - ``subscribed=True``  + channel='digest' → ⭐ only (일일 다이제스트)

    FR-SUB-002 / FR-SUB-003 invariants are enforced at the route layer so
    the DB stores whatever the API normalized.
    """

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", name="uq_user_category"),
    )

    id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        default=lambda: uuid.uuid4().hex,
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    subscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    # 'instant' | 'digest' | 'off'
    channel: Mapped[str] = mapped_column(String(8), default="off")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class AlertLog(Base):
    """Persisted record of every notifier send attempt (FR-NOTIF-005).

    ``item_id`` is nullable: digest emails group multiple items into one
    log row. ``status`` distinguishes ``sent`` / ``failed`` / ``skipped``
    (e.g. SMTP not configured).
    """

    __tablename__ = "alert_log"

    id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        default=lambda: uuid.uuid4().hex,
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    item_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("items.id"), nullable=True
    )
    # 'instant' | 'digest' | 'owner_failure'
    channel: Mapped[str] = mapped_column(String(16))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 'sent' | 'failed' | 'skipped'
    status: Mapped[str] = mapped_column(String(16))
    error_message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
