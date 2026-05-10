"""NEW item detection — SHA-256 hash + ``(site_id, url)`` dedupe.

Phase 1 only emits ``type='NEW'`` items (decision + spec — CHANGE detection
is Phase 2 work, gated on the snapshot table). The detector consumes a
list of ``CrawledItem`` and returns SQLAlchemy ``Item`` instances ready to
``session.add_all`` + commit.

Dedupe rules:

- Skip if the (site_id, url) tuple already exists in the ``items`` table
  (Step 2 introduced ``UniqueConstraint('site_id', 'url')``).
- Skip duplicates within the same input batch (e.g. when the same anchor
  appears twice in an HTML listing).

``Item.id`` is intentionally NOT supplied here — Step 3.1a added a
``default=lambda: uuid.uuid4().hex`` so SQLAlchemy auto-fills on insert.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Item
from monitor.watchtower.base import CrawledItem

logger = logging.getLogger(__name__)


def sha256_hash(content: str) -> str:
    """Return the hex SHA-256 of ``content`` (UTF-8 encoded). 64 chars."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def detect_new_items(
    session: Session, site_id: str, crawled: list[CrawledItem]
) -> list[Item]:
    """Build ``Item`` rows for each ``CrawledItem`` not already in the DB.

    Caller commits. Returns the list of new ``Item`` instances (already
    added to ``session`` via ``add_all``).
    """
    if not crawled:
        return []

    existing_urls: set[str] = set(
        session.execute(
            select(Item.url).where(Item.site_id == site_id)
        ).scalars().all()
    )

    new_items: list[Item] = []
    seen_in_batch: set[str] = set()
    now = datetime.now(timezone.utc)

    for crawled_item in crawled:
        url = (crawled_item.url or "").strip()
        if not url:
            continue
        if url in existing_urls or url in seen_in_batch:
            continue
        title = (crawled_item.title or "").strip() or "(제목 없음)"
        summary_raw = (crawled_item.summary or "").strip()
        item = Item(
            # id auto-filled by Item.id default=lambda: uuid.uuid4().hex
            site_id=site_id,
            type="NEW",
            title=title[:500],
            summary=(summary_raw[:2000] or None),
            url=url[:500],
            content_hash=sha256_hash(crawled_item.content_for_hash or url),
            detected_at=now,
            read_by="",
        )
        new_items.append(item)
        seen_in_batch.add(url)

    if new_items:
        session.add_all(new_items)
    return new_items
