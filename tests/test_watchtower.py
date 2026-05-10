"""Watchtower (Step 2) — DB models, seed, REST API, /ui smoke tests."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.models import Category, Item, Site, User
from app.db.seed import run_seed
from app.db.import_legacy import import_legacy_events


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_models_create_relationships(watchtower_db):
    """Category ↔ Site ↔ Item ↔ User round-trip + relationship traversal."""
    with watchtower_db() as session:
        # Insert in dependency order so FK PRAGMA enforcement is happy:
        # user → category (refs user) → site (refs category) → item (refs site).
        session.add(User(id="u1", name="운영자", dept="AX팀", email="ops@example.com", role="operator"))
        session.flush()
        session.add(Category(id="reg", name="금융 규제·감독", owner_dept="컴플라이언스", owner_user_id="u1"))
        session.flush()
        session.add(Site(
            id="s1", name="금융위원회", url="https://example.com",
            category_id="reg", crawl_method="html", content_selector=".x",
            crawl_interval_min=120, status="ok",
        ))
        session.flush()
        session.add(Item(
            id="i1", site_id="s1", type="NEW", title="제목", summary="요약",
            url="https://example.com/a", content_hash="h" * 64,
            detected_at=datetime.now(timezone.utc), read_by="",
        ))
        session.commit()

        loaded_cat = session.get(Category, "reg")
        assert loaded_cat is not None
        assert len(loaded_cat.sites) == 1
        assert loaded_cat.sites[0].id == "s1"
        assert len(loaded_cat.sites[0].items) == 1


def test_site_crawl_interval_clamp(watchtower_db):
    """FR-SITE-003 — values < 60 are clamped to 60."""
    with watchtower_db() as session:
        session.add(Category(id="reg", name="x", owner_dept="x"))
        session.commit()
        s = Site(
            id="s1", name="x", url="https://x", category_id="reg",
            crawl_method="html", crawl_interval_min=30,
        )
        assert s.crawl_interval_min == 60
        # Already-valid values pass through untouched.
        s2 = Site(
            id="s2", name="y", url="https://y", category_id="reg",
            crawl_method="html", crawl_interval_min=240,
        )
        assert s2.crawl_interval_min == 240


def test_item_read_by_helpers(watchtower_db):
    """`Item.mark_read` is idempotent and produces a sorted CSV."""
    item = Item(
        id="i1", site_id="s1", type="NEW", title="t", url="u",
        content_hash="h", detected_at=datetime.now(timezone.utc), read_by="",
    )
    assert item.read_by_set() == set()
    assert item.mark_read("u1") is True
    assert item.mark_read("u1") is False
    item.mark_read("u2")
    assert item.read_by == "u1,u2"
    assert item.is_read_by("u1") and item.is_read_by("u2")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def test_seed_loads_8_categories_30_sites_1_user(watchtower_db):
    with watchtower_db() as session:
        counts = run_seed(session)
        assert counts == {"categories": 8, "sites": 30, "users": 1}
        assert session.query(Category).count() == 8
        assert session.query(Site).count() == 30
        assert session.query(User).count() == 1


def test_seed_idempotent(watchtower_db):
    """Running run_seed twice yields the same row counts (no duplicate inserts)."""
    with watchtower_db() as session:
        run_seed(session)
        before = (
            session.query(Category).count(),
            session.query(Site).count(),
            session.query(User).count(),
        )
        counts2 = run_seed(session)
        after = (
            session.query(Category).count(),
            session.query(Site).count(),
            session.query(User).count(),
        )
        assert counts2 == {"categories": 0, "sites": 0, "users": 0}
        assert before == after


def test_seed_env_var_substitution(watchtower_db, monkeypatch):
    """`${WATCHTOWER_ADMIN_EMAIL:-...}` must substitute the env value."""
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "test-admin@corp.example")
    with watchtower_db() as session:
        run_seed(session)
        u = session.query(User).first()
        assert u is not None
        assert u.email == "test-admin@corp.example"


# ---------------------------------------------------------------------------
# REST API smokes
# ---------------------------------------------------------------------------


def test_api_categories_smoke(watchtower_app):
    client, sm = watchtower_app
    with sm() as session:
        run_seed(session)

    res = client.get("/api/categories")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 8
    sample = data[0]
    assert {"id", "name", "owner_dept", "sites_count", "item_count_unread"} <= sample.keys()


def test_api_sites_smoke(watchtower_app):
    client, sm = watchtower_app
    with sm() as session:
        run_seed(session)
    res = client.get("/api/sites")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 30
    assert all("category_id" in s for s in data)


def test_api_items_smoke(watchtower_app):
    """Empty DB → empty list; with items → sorted by read ASC, detected_at DESC."""
    client, sm = watchtower_app
    res = client.get("/api/items")
    assert res.status_code == 200
    assert res.json() == []

    with sm() as session:
        run_seed(session)
        # Insert 3 items: an old read item + a fresh unread + a slightly older unread.
        now = datetime.now(timezone.utc)
        session.add_all([
            Item(id="ix-read", site_id="s1", type="NEW", title="읽음", url="https://x/a",
                 content_hash="a" * 64, detected_at=now, read_by="u1"),
            Item(id="ix-fresh", site_id="s1", type="NEW", title="최신 미읽음", url="https://x/b",
                 content_hash="b" * 64, detected_at=now, read_by=""),
            Item(id="ix-older", site_id="s1", type="NEW", title="오래된 미읽음", url="https://x/c",
                 content_hash="c" * 64, detected_at=now - timedelta(hours=5), read_by=""),
        ])
        session.commit()

    res = client.get("/api/items?category=reg")
    assert res.status_code == 200
    arr = res.json()
    assert [r["id"] for r in arr] == ["ix-fresh", "ix-older", "ix-read"]
    # `read` flag is derived against the resolved-me user (admin@watchtower.local
    # by default). When me.id != 'u1', the read item still appears unread —
    # which is exactly what the prototype + spec want.

    # type filter
    arr_change = client.get("/api/items?type=CHANGE").json()
    assert arr_change == []


def test_api_items_limit_clamp(watchtower_app):
    client, sm = watchtower_app
    res = client.get("/api/items?limit=0")
    assert res.status_code == 422  # FastAPI validation

    res = client.get("/api/items?limit=2000")
    assert res.status_code == 422


def test_api_users_me_env_match(watchtower_app, monkeypatch):
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "ops-2@corp.example")
    with sm() as session:
        # Seeded user gets the env email substituted in.
        run_seed(session)
    res = client.get("/api/users/me")
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == "ops-2@corp.example"
    assert body["role"] == "operator"


def test_api_users_me_404_when_empty(watchtower_app):
    """No users seeded → 404 with a Korean error message."""
    client, _sm = watchtower_app
    res = client.get("/api/users/me")
    assert res.status_code == 404
    detail = res.json().get("detail", "")
    assert "사용자" in detail


def test_api_health_smoke(watchtower_app):
    client, sm = watchtower_app
    with sm() as session:
        run_seed(session)
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["db"] == "connected"
    assert body["sites_total"] == 30
    assert body["sites_failed"] == 0
    assert "uptime_seconds" in body and "now" in body


# ---------------------------------------------------------------------------
# /ui + /static
# ---------------------------------------------------------------------------


def test_ui_html_response(tmp_path, monkeypatch):
    """`/ui` returns the prototype HTML with the `Watchtower` brand string."""
    from fastapi.testclient import TestClient
    # Import main lazily because importing it boots the full lifespan when
    # used inside TestClient — we use `with TestClient(...)` to drive lifespan.
    import importlib

    monkeypatch.chdir(tmp_path)
    main_mod = importlib.import_module("main")
    with TestClient(main_mod.app) as client:
        res = client.get("/ui")
        assert res.status_code == 200
        assert "text/html" in res.headers.get("content-type", "")
        assert "Watchtower" in res.text


def test_static_files_mount(tmp_path, monkeypatch):
    """`/static/watchtower.html` is reachable from the StaticFiles mount."""
    from fastapi.testclient import TestClient
    import importlib

    monkeypatch.chdir(tmp_path)
    main_mod = importlib.import_module("main")
    with TestClient(main_mod.app) as client:
        res = client.get("/static/watchtower.html")
        assert res.status_code == 200
        assert "Watchtower" in res.text


# ---------------------------------------------------------------------------
# Legacy import (events.jsonl → Item)
# ---------------------------------------------------------------------------


def _write_legacy_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows) + "\n",
        encoding="utf-8",
    )


def test_legacy_import_idempotent(watchtower_db, tmp_path):
    """Running import twice on the same JSONL inserts the rows only once."""
    jsonl = tmp_path / "events.jsonl"
    fetched = datetime(2026, 5, 9, 10, 0, tzinfo=timezone.utc).isoformat()
    rows = [
        {
            "source": "dart",
            "external_id": "e1",
            "title": "공시 1",
            "url": "https://dart.example.com/1",
            "fetched_at": fetched,
            "published_at": fetched,
            "content_hash": "f" * 64,
            "summary": "요약 1",
        },
        {
            "source": "fsc",
            "external_id": "e2",
            "title": "FSC 공시",
            "url": "https://fsc.example.com/2",
            "fetched_at": fetched,
            "content_hash": "a" * 64,
            "summary": "요약 2",
        },
        {
            "source": "unknown",  # unmapped → skipped
            "external_id": "e3",
            "title": "skipped",
            "url": "https://unknown.example.com/3",
            "fetched_at": fetched,
            "content_hash": "z" * 64,
        },
    ]
    _write_legacy_jsonl(jsonl, rows)

    with watchtower_db() as session:
        run_seed(session)
        first = import_legacy_events(session, str(jsonl))
        second = import_legacy_events(session, str(jsonl))
        total = session.query(Item).count()

    assert first == 2
    assert second == 0
    assert total == 2
