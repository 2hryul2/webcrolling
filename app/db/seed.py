"""Seed Watchtower DB from `config/seed_*.yaml`.

`run_seed(session)` is idempotent: each row is upserted by primary key (skip
if id already exists). Returns counts so the caller can log a summary.

Environment variable substitution — `${VAR}` and `${VAR:-default}` are
expanded inside string fields after yaml load. This is intentionally limited
to a small regex (no shell-style `:?`, no nested expansions) to keep the
attack surface minimal.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.db.models import (
    CategorySubscription,
    Category,
    Site,
    SiteSubscription,
    User,
)

logger = logging.getLogger(__name__)

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _BASE_DIR / "config"

_CATEGORIES_PATH = _CONFIG_DIR / "seed_categories.yaml"
_SITES_PATH = _CONFIG_DIR / "seed_sites.yaml"
_USERS_PATH = _CONFIG_DIR / "seed_users.yaml"

# `${VAR}` or `${VAR:-default}` (default may be empty).
_VAR_RE = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    """Recursively substitute `${VAR}` / `${VAR:-default}` in strings."""
    if isinstance(value, str):
        def _sub(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2) or ""
            return os.environ.get(name, default)
        return _VAR_RE.sub(_sub, value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        logger.warning("Seed file missing: %s", path)
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return _expand_env(data)


def _seed_categories(session: Session, rows: list[dict[str, Any]]) -> int:
    """Insert categories that don't already exist. Returns count inserted."""
    existing = {row[0] for row in session.query(Category.id).all()}
    inserted = 0
    for row in rows:
        cid = row.get("id")
        if not cid or cid in existing:
            continue
        session.add(Category(
            id=cid,
            name=row.get("name", ""),
            owner_dept=row.get("owner_dept", ""),
            owner_user_id=row.get("owner_user_id"),
        ))
        inserted += 1
    return inserted


def _seed_sites(session: Session, rows: list[dict[str, Any]]) -> int:
    existing = {row[0] for row in session.query(Site.id).all()}
    inserted = 0
    for row in rows:
        sid = row.get("id")
        if not sid or sid in existing:
            continue
        session.add(Site(
            id=sid,
            name=row.get("name", ""),
            url=row.get("url", ""),
            category_id=row.get("category_id", ""),
            crawl_method=row.get("crawl_method", "html"),
            content_selector=row.get("content_selector"),
            crawl_interval_min=int(row.get("crawl_interval_min", 60) or 60),
            status=row.get("status", "ok"),
            enabled=bool(row.get("enabled", True)),
        ))
        inserted += 1
    return inserted


def _seed_category_subscriptions(session: Session) -> int:
    """Insert one ``CategorySubscription`` per (user, category) where missing.

    Default state is ``subscribed=False, channel='off'`` (Step 4 Decision §1
    — conservative prod policy; users opt in via the UI). Idempotent: an
    existing (user_id, category_id) pair is left untouched.
    """
    user_ids = [row[0] for row in session.query(User.id).all()]
    cat_ids = [row[0] for row in session.query(Category.id).all()]
    if not user_ids or not cat_ids:
        return 0

    existing_pairs = {
        (uid, cid)
        for uid, cid in session.query(
            CategorySubscription.user_id, CategorySubscription.category_id
        ).all()
    }

    inserted = 0
    for uid in user_ids:
        for cid in cat_ids:
            if (uid, cid) in existing_pairs:
                continue
            session.add(CategorySubscription(
                user_id=uid,
                category_id=cid,
                subscribed=False,
                channel="off",
            ))
            inserted += 1
    return inserted


def _seed_site_subscriptions(session: Session) -> int:
    """Insert one ``SiteSubscription`` per (user, site) where missing (FR-MIG-002).

    Default ``enabled=False`` — sites must be explicitly opted in to gate
    the AND filter (FR-NOTIF-009). Idempotent.
    """
    user_ids = [row[0] for row in session.query(User.id).all()]
    site_ids = [row[0] for row in session.query(Site.id).all()]
    if not user_ids or not site_ids:
        return 0

    existing_pairs = {
        (uid, sid)
        for uid, sid in session.query(
            SiteSubscription.user_id, SiteSubscription.site_id
        ).all()
    }

    inserted = 0
    for uid in user_ids:
        for sid in site_ids:
            if (uid, sid) in existing_pairs:
                continue
            session.add(SiteSubscription(
                user_id=uid,
                site_id=sid,
                enabled=False,
            ))
            inserted += 1
    return inserted


def create_default_site_subscriptions_for_new_site(
    session: Session, site_id: str
) -> int:
    """Step 5 hook — pre-fill SiteSubscription for every user when a new Site lands.

    Called by the future ``POST /api/sites`` admin endpoint (FR-SITE-002) right
    after the ``Site`` row is committed. For each existing user, inserts a
    ``SiteSubscription`` with ``enabled = CategorySubscription.subscribed`` for
    the site's category — matching the user's confirmed default policy
    (ideation §7-b → "카테고리 별 ⭐ 상태 따름").

    Idempotent: existing (user, site) pairs are skipped.

    Returns the count of rows actually inserted.
    """
    site = session.get(Site, site_id)
    if site is None:
        return 0

    user_ids = [row[0] for row in session.query(User.id).all()]
    if not user_ids:
        return 0

    existing_users = {
        sub.user_id
        for sub in session.query(SiteSubscription).filter_by(site_id=site_id).all()
    }

    # Pull category subscription state for the site's category, per user.
    cat_subs = {
        sub.user_id: bool(sub.subscribed)
        for sub in session.query(CategorySubscription).filter_by(
            category_id=site.category_id
        ).all()
    }

    inserted = 0
    for uid in user_ids:
        if uid in existing_users:
            continue
        enabled = cat_subs.get(uid, False)
        session.add(SiteSubscription(
            user_id=uid,
            site_id=site_id,
            enabled=enabled,
        ))
        inserted += 1

    if inserted:
        session.flush()
    return inserted


def _seed_users(session: Session, rows: list[dict[str, Any]]) -> int:
    existing = {row[0] for row in session.query(User.id).all()}
    inserted = 0
    for row in rows:
        uid = row.get("id")
        if not uid or uid in existing:
            continue
        session.add(User(
            id=uid,
            name=row.get("name", ""),
            dept=row.get("dept", ""),
            email=row.get("email", ""),
            messenger_id=row.get("messenger_id"),
            role=row.get("role", "member"),
        ))
        inserted += 1
    return inserted


def run_seed(session: Session) -> dict[str, int]:
    """Load seed yaml files and insert missing rows.

    Order matters: categories first (FK target), then sites + users.
    Returns: ``{"categories": N, "sites": N, "users": N}``.
    """
    cat_rows = _load_yaml(_CATEGORIES_PATH).get("categories", []) or []
    site_rows = _load_yaml(_SITES_PATH).get("sites", []) or []
    user_rows = _load_yaml(_USERS_PATH).get("users", []) or []

    counts = {
        "categories": _seed_categories(session, cat_rows),
        "users": _seed_users(session, user_rows),
        "sites": _seed_sites(session, site_rows),
    }
    # Flush pending category/user/site rows so the subscription matrices see them.
    session.flush()
    counts["category_subscriptions"] = _seed_category_subscriptions(session)
    counts["site_subscriptions"] = _seed_site_subscriptions(session)
    # Legacy key retained for callers that grep counts["subscriptions"].
    counts["subscriptions"] = counts["category_subscriptions"]
    session.commit()
    if any(counts.values()):
        logger.info(
            "[seed] inserted: %d categories / %d sites / %d users / "
            "%d category_subscriptions / %d site_subscriptions",
            counts["categories"], counts["sites"], counts["users"],
            counts["category_subscriptions"], counts["site_subscriptions"],
        )
    else:
        logger.info("[seed] up-to-date — no rows inserted")
    return counts
