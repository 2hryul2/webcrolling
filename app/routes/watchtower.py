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
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.models import Category, Item, Site, User
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
