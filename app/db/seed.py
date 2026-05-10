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

from app.db.models import Category, Site, Subscription, User

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


def _seed_subscriptions(session: Session) -> int:
    """Insert one ``Subscription`` per (user, category) where missing.

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
            Subscription.user_id, Subscription.category_id
        ).all()
    }

    inserted = 0
    for uid in user_ids:
        for cid in cat_ids:
            if (uid, cid) in existing_pairs:
                continue
            session.add(Subscription(
                user_id=uid,
                category_id=cid,
                subscribed=False,
                channel="off",
            ))
            inserted += 1
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
    # Flush pending category/user rows so the subscription matrix sees them.
    session.flush()
    counts["subscriptions"] = _seed_subscriptions(session)
    session.commit()
    if any(counts.values()):
        logger.info(
            "[seed] inserted: %d categories / %d sites / %d users / %d subscriptions",
            counts["categories"], counts["sites"], counts["users"],
            counts["subscriptions"],
        )
    else:
        logger.info("[seed] up-to-date — no rows inserted")
    return counts
