"""Watchtower REST API — 5 read-only GET endpoints under `/api/*`.

All responses are JSON, UTF-8. Mutations (mark-read, subscriptions, etc.)
are deferred to Step 4 (per ARCHITECT-BRIEF §"Out of Scope").

Endpoints:

- ``GET /api/categories`` — 8 categories with sites_count + item_count_unread
- ``GET /api/sites``      — 30 sites
- ``GET /api/items``      — items with optional ``category`` / ``type`` /
  ``limit`` filters; sorted by ``read ASC, detected_at DESC`` (FR-FEED-004)
- ``GET /api/users/me``   — single Phase 1 user resolved via env var
- ``GET /api/health``     — DB liveness + site-status counters
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import (
    AlertLog,
    Category,
    Item,
    Site,
    Subscription,
    User,
)
from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["watchtower"])

_START_TIME = time.time()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_me(session: Session) -> Optional[User]:
    """Resolve the single Phase 1 user.

    Match order: ``WATCHTOWER_ADMIN_EMAIL`` env var → first user by id.
    """
    admin_email = os.getenv("WATCHTOWER_ADMIN_EMAIL", "").strip()
    if admin_email:
        u = session.scalar(select(User).where(User.email == admin_email))
        if u is not None:
            return u
    return session.scalar(select(User).order_by(User.id).limit(1))


def _iso_utc(value: Optional[datetime]) -> Optional[str]:
    """Render a datetime as ISO 8601 UTC with `Z` suffix (Flag §5)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# /api/categories
# ---------------------------------------------------------------------------


@router.get("/categories")
def list_categories(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Return all categories with `sites_count` + `item_count_unread`.

    `item_count_unread` is computed against the resolved "me" user — items
    where ``me.id`` is absent from the CSV ``read_by`` column.
    """
    me = _resolve_me(session)
    me_id = me.id if me else ""

    # categories + site count via single GROUP BY query.
    sites_count_rows = dict(
        session.execute(
            select(Site.category_id, func.count(Site.id)).group_by(Site.category_id)
        ).all()
    )

    # Items joined to sites — load just (category_id, read_by) for tally.
    item_rows = session.execute(
        select(Site.category_id, Item.read_by).join(Item, Item.site_id == Site.id)
    ).all()

    unread_by_cat: dict[str, int] = {}
    for cat_id, read_by in item_rows:
        if not _is_read_by(read_by, me_id):
            unread_by_cat[cat_id] = unread_by_cat.get(cat_id, 0) + 1

    cats = session.scalars(select(Category).order_by(Category.id)).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "owner_dept": c.owner_dept,
            "sites_count": int(sites_count_rows.get(c.id, 0)),
            "item_count_unread": int(unread_by_cat.get(c.id, 0)),
        }
        for c in cats
    ]


def _is_read_by(read_by_csv: Optional[str], user_id: str) -> bool:
    """Mirror of `Item.is_read_by` for raw CSV values pulled from queries."""
    if not user_id or not read_by_csv:
        return False
    return user_id in {tok.strip() for tok in read_by_csv.split(",") if tok.strip()}


# ---------------------------------------------------------------------------
# /api/sites
# ---------------------------------------------------------------------------


@router.get("/sites")
def list_sites(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Return all sites (no pagination — Phase 1 has 30)."""
    sites = session.scalars(select(Site).order_by(Site.id)).all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "category_id": s.category_id,
            "crawl_method": s.crawl_method,
            "status": s.status,
            "last_ok_at": _iso_utc(s.last_ok_at),
        }
        for s in sites
    ]


# ---------------------------------------------------------------------------
# /api/items
# ---------------------------------------------------------------------------


@router.get("/items")
def list_items(
    session: Session = Depends(get_session),
    category: Optional[str] = Query(default=None),
    type: Optional[str] = Query(default=None, alias="type"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Return items with optional ``category`` / ``type`` filters.

    Sort: ``read ASC, detected_at DESC`` (FR-FEED-004). The ``read`` flag is
    derived against the resolved "me" user.
    """
    me = _resolve_me(session)
    me_id = me.id if me else ""

    stmt = select(Item, Site).join(Site, Item.site_id == Site.id)
    if category:
        stmt = stmt.where(Site.category_id == category)
    if type:
        stmt = stmt.where(Item.type == type)
    # Pull a generous batch and sort in Python — `read` depends on me_id and
    # is awkward to express in pure SQL with the CSV column. Phase 1 volume
    # is small (200 default cap), so this is intentional.
    stmt = stmt.order_by(Item.detected_at.desc()).limit(max(limit * 2, limit))

    rows = session.execute(stmt).all()

    enriched: list[dict[str, Any]] = []
    for item, site in rows:
        read = _is_read_by(item.read_by, me_id)
        enriched.append({
            "id": item.id,
            "site_id": item.site_id,
            "site_name": site.name,
            "category_id": site.category_id,
            "type": item.type,
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
            "detected_at": _iso_utc(item.detected_at),
            "read": read,
        })

    enriched.sort(key=lambda r: (r["read"], _detected_sort_key(r["detected_at"])))
    return enriched[:limit]


def _detected_sort_key(iso: Optional[str]) -> float:
    """Negative epoch seconds so DESC sort works with ASC tuple ordering."""
    if not iso:
        return 0.0
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return -dt.timestamp()
    except ValueError:
        return 0.0


# ---------------------------------------------------------------------------
# /api/users/me
# ---------------------------------------------------------------------------


@router.get("/users/me")
def get_me(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Return the resolved Phase 1 user."""
    me = _resolve_me(session)
    if me is None:
        raise HTTPException(status_code=404, detail="등록된 사용자가 없습니다")
    return {
        "id": me.id,
        "name": me.name,
        "dept": me.dept,
        "email": me.email,
        "role": me.role,
    }


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Return DB liveness + aggregate site-status counters."""
    db_state = "connected"
    sites_total = 0
    sites_failed = 0
    try:
        sites_total = int(session.scalar(select(func.count(Site.id))) or 0)
        sites_failed = int(
            session.scalar(
                select(func.count(Site.id)).where(Site.status == "failed")
            ) or 0
        )
    except SQLAlchemyError as exc:
        logger.warning("DB health check failed: %s", exc)
        db_state = "error"

    return {
        "ok": db_state == "connected",
        "db": db_state,
        "sites_total": sites_total,
        "sites_failed": sites_failed,
        "uptime_seconds": int(time.time() - _START_TIME),
        "now": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# /api/subscriptions  (Step 4 — FR-SUB-001~004)
# ---------------------------------------------------------------------------


_VALID_CHANNELS = ("instant", "digest", "off")


class SubscriptionPatch(BaseModel):
    """Body schema for ``PATCH /api/subscriptions/{category_id}``."""

    model_config = ConfigDict(extra="forbid")

    subscribed: Optional[bool] = None
    channel: Optional[str] = Field(default=None)


def _serialize_subscription(category_id: str, sub: Optional[Subscription]) -> dict[str, Any]:
    if sub is None:
        return {
            "category_id": category_id,
            "subscribed": False,
            "channel": "off",
            "updated_at": None,
        }
    return {
        "category_id": category_id,
        "subscribed": bool(sub.subscribed),
        "channel": sub.channel or "off",
        "updated_at": _iso_utc(sub.updated_at),
    }


@router.get("/subscriptions")
def list_subscriptions(
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """Return one row per category for the resolved "me" user.

    Categories with no Subscription row yet are returned with default
    ``subscribed=False, channel='off'`` so the UI can render all 8 sidebar
    entries on first load (FR-SUB-004 — me.id only).
    """
    me = _resolve_me(session)
    if me is None:
        raise HTTPException(status_code=404, detail="등록된 사용자가 없습니다")

    cats = session.scalars(select(Category).order_by(Category.id)).all()
    existing = {
        sub.category_id: sub
        for sub in session.scalars(
            select(Subscription).where(Subscription.user_id == me.id)
        ).all()
    }
    return [_serialize_subscription(c.id, existing.get(c.id)) for c in cats]


@router.patch("/subscriptions/{category_id}")
def patch_subscription(
    category_id: str,
    body: SubscriptionPatch,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Upsert a (me, category) subscription. Enforces FR-SUB-002/003.

    Invariants applied here:

    - ``subscribed=False`` → ``channel='off'`` (FR-SUB-002).
    - ``channel ∈ {'instant', 'digest'}`` → ``subscribed=True`` (FR-SUB-003).
    - ``channel='off'`` is allowed without flipping ``subscribed``.
    """
    me = _resolve_me(session)
    if me is None:
        raise HTTPException(status_code=404, detail="등록된 사용자가 없습니다")

    cat = session.get(Category, category_id)
    if cat is None:
        raise HTTPException(status_code=404, detail="존재하지 않는 카테고리입니다")

    if body.channel is not None and body.channel not in _VALID_CHANNELS:
        raise HTTPException(
            status_code=422,
            detail=f"channel은 {_VALID_CHANNELS} 중 하나여야 합니다",
        )

    sub = session.scalar(
        select(Subscription).where(
            Subscription.user_id == me.id,
            Subscription.category_id == category_id,
        )
    )
    if sub is None:
        sub = Subscription(
            user_id=me.id,
            category_id=category_id,
            subscribed=False,
            channel="off",
        )
        session.add(sub)

    # Apply patch fields. Explicit ``subscribed=False`` is the authoritative
    # signal for FR-SUB-002 — wipe the channel before the post-apply
    # invariants run, otherwise a lingering 'instant'/'digest' would flip
    # subscribed back on.
    if body.subscribed is not None:
        sub.subscribed = bool(body.subscribed)
        if not sub.subscribed:
            sub.channel = "off"
    if body.channel is not None:
        sub.channel = body.channel

    # Enforce invariants AFTER the patch is applied.
    if sub.channel in ("instant", "digest"):
        sub.subscribed = True  # FR-SUB-003
    if not sub.subscribed:
        sub.channel = "off"    # FR-SUB-002

    session.flush()
    session.commit()
    session.refresh(sub)
    return _serialize_subscription(category_id, sub)


# ---------------------------------------------------------------------------
# /api/items/{item_id}/read  (Step 4 — FR-FEED-006)
# ---------------------------------------------------------------------------


@router.patch("/items/{item_id}/read")
def mark_item_read(
    item_id: str,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Append the resolved "me" user id to ``Item.read_by`` (idempotent)."""
    me = _resolve_me(session)
    if me is None:
        raise HTTPException(status_code=404, detail="등록된 사용자가 없습니다")

    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="존재하지 않는 항목입니다")

    item.mark_read(me.id)  # mark_read is itself idempotent
    session.commit()
    return {"id": item.id, "read": True}


# ---------------------------------------------------------------------------
# /api/alert-log  (Step 4 — FR-NOTIF-005, UI exposure deferred to Step 5)
# ---------------------------------------------------------------------------


@router.get("/alert-log")
def list_alert_log(
    session: Session = Depends(get_session),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """Return the resolved "me" user's alert log rows (most recent first).

    Phase 1 enforces user separation here even though there is exactly one
    row — this keeps the contract aligned with FR-SUB-004.
    """
    me = _resolve_me(session)
    if me is None:
        raise HTTPException(status_code=404, detail="등록된 사용자가 없습니다")

    rows = session.scalars(
        select(AlertLog)
        .where(AlertLog.user_id == me.id)
        .order_by(AlertLog.sent_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "item_id": r.item_id,
            "channel": r.channel,
            "sent_at": _iso_utc(r.sent_at),
            "status": r.status,
            "error_message": r.error_message,
            "detail": r.detail,
        }
        for r in rows
    ]
