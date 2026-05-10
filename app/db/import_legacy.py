"""Best-effort one-shot import of Step 1 `events.jsonl` rows into Watchtower `Item`s.

Mapping (intentionally simple — Decision §6, refined in Step 3):

- ``source == 'dart' or 'fsc'`` → ``site_id = 's1'`` (금융위원회), ``category_id = 'reg'``
- Item ID := Step 1 ``content_hash`` (32-hex prefix) for stable idempotency
- ``detected_at`` := Step 1 ``fetched_at`` (or ``published_at`` fallback)

Deduplication relies on the ``UniqueConstraint("site_id", "url")`` from
``app.db.models.Item`` — duplicate ``(site_id, url)`` pairs are skipped via a
pre-check (cheaper than catching IntegrityError per row).

If the JSONL file is missing, malformed, or any single row fails to map, we
warn and continue — startup MUST proceed (see ARCHITECT-BRIEF §4 / §6).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.database import load_jsonl
from app.db.models import Item, Site

logger = logging.getLogger(__name__)

# Step 1 source → (site_id, category_id) — demo mapping only.
_SOURCE_TO_SITE: dict[str, tuple[str, str]] = {
    "dart": ("s1", "reg"),
    "fsc": ("s1", "reg"),
}


def _parse_dt(value: object) -> Optional[datetime]:
    """Parse an ISO 8601 string (or pass through datetime) → tz-aware datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _stable_item_id(content_hash: str, fallback_url: str) -> str:
    """Pick a stable 32-hex item id for legacy rows.

    Prefers a 32-char prefix of the SHA-256 ``content_hash`` so two import
    runs on the same JSONL produce the same Item.id. Falls back to a UUID
    derived from the row's URL when ``content_hash`` is missing/short.
    """
    if content_hash and len(content_hash) >= 32:
        return content_hash[:32]
    return uuid.uuid5(uuid.NAMESPACE_URL, fallback_url or content_hash or "").hex


def import_legacy_events(session: Session, jsonl_path: str) -> int:
    """Import Step 1 events.jsonl rows into the ``items`` table.

    Returns the count of rows newly inserted. Idempotent — running twice on
    the same file inserts only the first run's net-new rows.
    """
    path = Path(jsonl_path)
    if not path.exists():
        logger.info("Legacy events.jsonl not found at %s — skipping import", path)
        return 0

    try:
        rows = load_jsonl(str(path))
    except Exception as exc:
        logger.warning("Legacy import: load_jsonl failed (%s)", exc)
        return 0

    if not rows:
        return 0

    # Cache existing site IDs once so we can skip rows whose target site is
    # not seeded yet (defensive — happens on a fresh DB the very first time).
    known_site_ids = {sid for (sid,) in session.query(Site.id).all()}

    # Pre-load existing (site_id, url) tuples for the legacy target sites.
    target_sites = {sid for (sid, _cat) in _SOURCE_TO_SITE.values()}
    existing_keys: set[tuple[str, str]] = set()
    if target_sites:
        q = session.query(Item.site_id, Item.url).filter(Item.site_id.in_(target_sites))
        existing_keys = {(s, u) for (s, u) in q.all()}

    inserted = 0
    seen_in_batch: set[tuple[str, str]] = set()

    for raw in rows:
        if not isinstance(raw, dict):
            continue
        source = (raw.get("source") or "").lower()
        mapping = _SOURCE_TO_SITE.get(source)
        if not mapping:
            continue
        site_id, category_id = mapping
        if site_id not in known_site_ids:
            # Site not seeded yet — skip silently (rare, seed runs first).
            continue

        url = raw.get("url") or ""
        if not url:
            continue
        key = (site_id, url)
        if key in existing_keys or key in seen_in_batch:
            continue

        title = (raw.get("title") or "").strip() or "(제목 없음)"
        summary = raw.get("summary")
        content_hash = (raw.get("content_hash") or "").strip()
        detected_at = (
            _parse_dt(raw.get("fetched_at"))
            or _parse_dt(raw.get("published_at"))
            or datetime.now(timezone.utc)
        )

        try:
            session.add(Item(
                id=_stable_item_id(content_hash, url),
                site_id=site_id,
                type="NEW",
                title=title[:500],
                summary=(summary or "")[:2000] or None,
                url=url[:500],
                content_hash=(content_hash or "")[:64] or "legacy",
                detected_at=detected_at,
                read_by="",
            ))
            seen_in_batch.add(key)
            inserted += 1
        except Exception as exc:  # row-level failure must not abort the import
            logger.warning("Legacy row skipped (%s): %s", source, exc)
            continue

    if inserted:
        try:
            session.commit()
            logger.info(
                "[legacy-import] %d items mapped from %s (categories=reg)",
                inserted, path,
            )
        except Exception as exc:
            session.rollback()
            logger.warning("Legacy import commit failed: %s", exc)
            return 0
    else:
        # Nothing to commit but still flush any pending state from queries.
        session.rollback()
    return inserted
