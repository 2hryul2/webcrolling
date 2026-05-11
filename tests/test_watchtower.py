"""Watchtower (Step 2) — DB models, seed, REST API, /ui smoke tests."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.models import (
    AlertLog,
    CategorySubscription,
    Category,
    Item,
    Site,
    SiteSubscription,
    User,
)
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
        # Step 4 — CategorySubscription default rows (1 user × 8 categories) added.
        # Step 4.5 — SiteSubscription default rows (1 user × 30 sites) added.
        assert counts == {
            "categories": 8,
            "sites": 30,
            "users": 1,
            "category_subscriptions": 8,
            "site_subscriptions": 30,
            "subscriptions": 8,  # legacy alias for category_subscriptions
        }
        assert session.query(Category).count() == 8
        assert session.query(Site).count() == 30
        assert session.query(User).count() == 1
        assert session.query(CategorySubscription).count() == 8
        assert session.query(SiteSubscription).count() == 30


def test_seed_idempotent(watchtower_db):
    """Running run_seed twice yields the same row counts (no duplicate inserts)."""
    with watchtower_db() as session:
        run_seed(session)
        before = (
            session.query(Category).count(),
            session.query(Site).count(),
            session.query(User).count(),
            session.query(CategorySubscription).count(),
            session.query(SiteSubscription).count(),
        )
        counts2 = run_seed(session)
        after = (
            session.query(Category).count(),
            session.query(Site).count(),
            session.query(User).count(),
            session.query(CategorySubscription).count(),
            session.query(SiteSubscription).count(),
        )
        assert counts2 == {
            "categories": 0,
            "sites": 0,
            "users": 0,
            "category_subscriptions": 0,
            "site_subscriptions": 0,
            "subscriptions": 0,
        }
        assert before == after


def test_seed_site_subscriptions_creates_30_rows(watchtower_db):
    """FR-MIG-002 — _seed_site_subscriptions creates one row per (user, site)."""
    from app.db.seed import _seed_site_subscriptions

    with watchtower_db() as session:
        # Run the regular seed to populate users + sites first.
        run_seed(session)
        # All 30 SiteSubscription rows should be present after run_seed.
        assert session.query(SiteSubscription).count() == 30
        # All default to enabled=False.
        enabled_rows = session.query(SiteSubscription).filter_by(enabled=True).count()
        assert enabled_rows == 0
        # Calling the helper again is idempotent.
        added = _seed_site_subscriptions(session)
        assert added == 0
        assert session.query(SiteSubscription).count() == 30


def test_create_default_site_subscriptions_for_new_site_inherits_category(
    watchtower_db,
):
    """Step 5 hook — new Site triggers per-user SiteSubscription defaults that
    inherit the user's CategorySubscription.subscribed flag (ideation §7-b)."""
    from app.db.seed import create_default_site_subscriptions_for_new_site

    with watchtower_db() as session:
        run_seed(session)
        # User subscribes to 'ai' category (star ON) but not 'reg'.
        ai_sub = session.query(CategorySubscription).filter_by(
            user_id="u1", category_id="ai"
        ).one()
        ai_sub.subscribed = True
        ai_sub.channel = "instant"
        session.commit()

        # Add a new Site to 'ai' — should auto-enable for u1.
        session.add(Site(
            id="s_new_ai", name="신규 AI 사이트", url="https://new-ai.example.com",
            category_id="ai", crawl_method="rss", crawl_interval_min=120,
            status="ok", enabled=True,
        ))
        session.commit()
        inserted_ai = create_default_site_subscriptions_for_new_site(
            session, "s_new_ai"
        )
        assert inserted_ai == 1

        ai_sub_row = session.query(SiteSubscription).filter_by(
            user_id="u1", site_id="s_new_ai"
        ).one()
        assert ai_sub_row.enabled is True

        # Add a new Site to 'reg' — should NOT auto-enable for u1 (not subscribed).
        session.add(Site(
            id="s_new_reg", name="신규 reg 사이트", url="https://new-reg.example.com",
            category_id="reg", crawl_method="html", crawl_interval_min=120,
            status="ok", enabled=True,
        ))
        session.commit()
        inserted_reg = create_default_site_subscriptions_for_new_site(
            session, "s_new_reg"
        )
        assert inserted_reg == 1

        reg_sub_row = session.query(SiteSubscription).filter_by(
            user_id="u1", site_id="s_new_reg"
        ).one()
        assert reg_sub_row.enabled is False

        # Calling the helper again is idempotent.
        again = create_default_site_subscriptions_for_new_site(session, "s_new_ai")
        assert again == 0


def test_create_default_site_subscriptions_for_unknown_site_noop(watchtower_db):
    """Helper is a NO-OP when the Site row doesn't exist."""
    from app.db.seed import create_default_site_subscriptions_for_new_site

    with watchtower_db() as session:
        run_seed(session)
        assert create_default_site_subscriptions_for_new_site(
            session, "no_such_site"
        ) == 0


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


# ---------------------------------------------------------------------------
# Step 4 — /api/subscriptions
# ---------------------------------------------------------------------------


def test_api_subscriptions_get_returns_8_rows(watchtower_app, monkeypatch):
    """Empty subscriptions table → still returns 8 rows (default-filled)."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
        # Wipe subscriptions to confirm the GET still returns 8 default rows.
        session.query(CategorySubscription).delete()
        session.commit()

    res = client.get("/api/category-subscriptions")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 8
    for row in data:
        assert {"category_id", "subscribed", "channel"} <= row.keys()
        assert row["subscribed"] is False
        assert row["channel"] == "off"


def test_api_subscriptions_patch_creates(watchtower_app, monkeypatch):
    """PATCH on a category with no CategorySubscription row inserts one."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
        session.query(CategorySubscription).delete()
        session.commit()

    res = client.patch("/api/category-subscriptions/reg", json={"subscribed": True})
    assert res.status_code == 200
    body = res.json()
    assert body == {
        "category_id": "reg",
        "subscribed": True,
        "channel": "off",
        "updated_at": body["updated_at"],
    }
    with sm() as session:
        rows = session.query(CategorySubscription).filter_by(category_id="reg").all()
        assert len(rows) == 1
        assert rows[0].subscribed is True
        assert rows[0].channel == "off"


def test_api_subscriptions_patch_updates(watchtower_app, monkeypatch):
    """Successive PATCH calls update updated_at and current state."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)

    # First PATCH — channel=instant; FR-SUB-003 forces subscribed=True.
    first = client.patch("/api/category-subscriptions/ai", json={"channel": "instant"}).json()
    assert first["subscribed"] is True
    assert first["channel"] == "instant"

    # Second PATCH — flip subscribed off; FR-SUB-002 forces channel='off'.
    second = client.patch("/api/category-subscriptions/ai", json={"subscribed": False}).json()
    assert second["subscribed"] is False
    assert second["channel"] == "off"
    # updated_at should advance.
    assert second["updated_at"] >= first["updated_at"]


def test_api_subscriptions_unsubscribe_forces_off(watchtower_app, monkeypatch):
    """FR-SUB-002 — subscribed=False normalizes channel to 'off'."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)

    # Pre-load with channel='digest'.
    client.patch("/api/category-subscriptions/comp", json={"channel": "digest"})
    # Now turn subscribed off.
    res = client.patch("/api/category-subscriptions/comp", json={"subscribed": False})
    body = res.json()
    assert body["subscribed"] is False
    assert body["channel"] == "off"


def test_api_subscriptions_instant_forces_subscribe(watchtower_app, monkeypatch):
    """FR-SUB-003 — channel='instant' or 'digest' forces subscribed=True."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
        # Ensure baseline is unsubscribed.
        session.query(CategorySubscription).delete()
        session.commit()

    res = client.patch("/api/category-subscriptions/sec", json={"channel": "instant"})
    body = res.json()
    assert body["subscribed"] is True
    assert body["channel"] == "instant"


def test_api_subscriptions_validates_channel_enum(watchtower_app, monkeypatch):
    """Bad channel values are rejected with 422."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)

    res = client.patch("/api/category-subscriptions/reg", json={"channel": "invalid"})
    assert res.status_code == 422


def test_api_subscriptions_unknown_category_404(watchtower_app, monkeypatch):
    """PATCH against a non-existent category id → 404."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)

    res = client.patch("/api/category-subscriptions/no_such_cat", json={"subscribed": True})
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Step 4 — PATCH /api/items/{id}/read
# ---------------------------------------------------------------------------


def test_api_items_read_marks_user(watchtower_app, monkeypatch):
    """PATCH /api/items/{id}/read appends me.id to read_by."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
        session.add(Item(
            id="iread1", site_id="s2", type="NEW", title="r", url="https://x/r",
            content_hash="r" * 64, detected_at=datetime.now(timezone.utc), read_by="",
        ))
        session.commit()

    res = client.patch("/api/items/iread1/read")
    assert res.status_code == 200
    body = res.json()
    assert body == {"id": "iread1", "read": True}

    with sm() as session:
        item = session.get(Item, "iread1")
        assert item.is_read_by("u1")


def test_api_items_read_idempotent(watchtower_app, monkeypatch):
    """Calling PATCH twice produces the same read_by set."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
        session.add(Item(
            id="iread2", site_id="s2", type="NEW", title="r", url="https://x/r2",
            content_hash="q" * 64, detected_at=datetime.now(timezone.utc), read_by="",
        ))
        session.commit()

    client.patch("/api/items/iread2/read")
    client.patch("/api/items/iread2/read")
    with sm() as session:
        item = session.get(Item, "iread2")
        assert item.read_by == "u1"  # exactly once


def test_api_items_read_404(watchtower_app, monkeypatch):
    """Non-existent item → 404."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)

    res = client.patch("/api/items/no_such_item/read")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Step 4 — /api/alert-log
# ---------------------------------------------------------------------------


def test_api_alert_log_returns_user_rows_only(watchtower_app, monkeypatch):
    """GET /api/alert-log only returns rows owned by the resolved me user."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
        session.add(User(id="u2", name="other", dept="x", email="other@example.com"))
        session.flush()  # ensure u2 lands before FK-constrained alert_log rows
        now = datetime.now(timezone.utc)
        session.add_all([
            AlertLog(id="a1", user_id="u1", item_id=None, channel="instant",
                     sent_at=now, status="sent"),
            AlertLog(id="a2", user_id="u1", item_id=None, channel="digest",
                     sent_at=now, status="skipped",
                     error_message="SMTP not configured"),
            AlertLog(id="a3", user_id="u2", item_id=None, channel="instant",
                     sent_at=now, status="sent"),
        ])
        session.commit()

    res = client.get("/api/alert-log")
    assert res.status_code == 200
    data = res.json()
    ids = sorted(r["id"] for r in data)
    assert ids == ["a1", "a2"]


def test_api_alert_log_limit_clamp(watchtower_app, monkeypatch):
    """limit=0 / limit>1000 → 422."""
    client, sm = watchtower_app
    with sm() as session:
        run_seed(session)
    assert client.get("/api/alert-log?limit=0").status_code == 422
    assert client.get("/api/alert-log?limit=2000").status_code == 422


# ---------------------------------------------------------------------------
# Step 4.5 — /api/site-subscriptions
# ---------------------------------------------------------------------------


def test_api_site_subscriptions_get_returns_30_default(watchtower_app, monkeypatch):
    """FR-SUB-008 — GET returns 30 rows with enabled=False after seed."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)

    res = client.get("/api/site-subscriptions")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) == 30
    assert all({"site_id", "enabled", "updated_at"} <= row.keys() for row in data)
    assert all(row["enabled"] is False for row in data)


def test_api_site_subscriptions_patch_upsert(watchtower_app, monkeypatch):
    """FR-SUB-006 — PATCH flips enabled and refreshes updated_at."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)

    res = client.patch("/api/site-subscriptions/s2", json={"enabled": True})
    assert res.status_code == 200
    body = res.json()
    assert body["site_id"] == "s2"
    assert body["enabled"] is True
    assert body["updated_at"]
    with sm() as session:
        row = session.query(SiteSubscription).filter_by(site_id="s2").one()
        assert row.enabled is True


def test_api_site_subscriptions_patch_system_disabled_returns_422(
    watchtower_app, monkeypatch
):
    """FR-SUB-007 — enabling a Site.enabled=False site is rejected with 422."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
        # s1 is seeded with enabled=False (사내망 가정으로 disabled). Force it
        # so the test is robust to future seed_sites.yaml edits.
        site = session.get(Site, "s1")
        site.enabled = False
        session.commit()

    res = client.patch("/api/site-subscriptions/s1", json={"enabled": True})
    assert res.status_code == 422
    detail = res.json().get("detail", "")
    assert "관리자" in detail or "비활성화" in detail
    with sm() as session:
        row = session.query(SiteSubscription).filter_by(site_id="s1").one()
        # Default seed left enabled=False; the rejected PATCH must not change it.
        assert row.enabled is False


def test_api_site_subscriptions_patch_disable_always_ok(watchtower_app, monkeypatch):
    """{enabled: false} is allowed regardless of system_enabled."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
        # First enable a site at the user level so disabling has effect.
        session.add(SiteSubscription(user_id="u1", site_id="s2", enabled=True))
        try:
            session.commit()
        except Exception:
            session.rollback()
            row = session.query(SiteSubscription).filter_by(
                user_id="u1", site_id="s2"
            ).one()
            row.enabled = True
            session.commit()
        # Force system_enabled=False on this site.
        s2 = session.get(Site, "s2")
        s2.enabled = False
        session.commit()

    res = client.patch("/api/site-subscriptions/s2", json={"enabled": False})
    assert res.status_code == 200
    assert res.json()["enabled"] is False


def test_api_site_subscriptions_unknown_site_404(watchtower_app, monkeypatch):
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
    res = client.patch("/api/site-subscriptions/no_such_site", json={"enabled": True})
    assert res.status_code == 404


def test_api_categories_cid_sites_joins_user_state(watchtower_app, monkeypatch):
    """FR-SUB-009 — joined response carries enabled_user + system_enabled."""
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
        # Mark s17 enabled for u1 so the join reflects per-user state.
        row = session.query(SiteSubscription).filter_by(
            user_id="u1", site_id="s17"
        ).one()
        row.enabled = True
        session.commit()

    res = client.get("/api/categories/ai/sites")
    assert res.status_code == 200
    body = res.json()
    assert body["category_id"] == "ai"
    by_id = {s["id"]: s for s in body["sites"]}
    assert "s17" in by_id
    assert by_id["s17"]["enabled_user"] is True
    assert by_id["s17"]["system_enabled"] is True
    # An untouched site (e.g. s18) should be enabled_user=False.
    if "s18" in by_id:
        assert by_id["s18"]["enabled_user"] is False


def test_api_categories_unknown_cid_404(watchtower_app, monkeypatch):
    client, sm = watchtower_app
    monkeypatch.setenv("WATCHTOWER_ADMIN_EMAIL", "admin@watchtower.local")
    with sm() as session:
        run_seed(session)
    res = client.get("/api/categories/no_such_cat/sites")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Step 4.5 — migration_subscriptions_to_category_subscriptions
# ---------------------------------------------------------------------------


def test_migration_when_old_table_absent_noop(tmp_path):
    """FR-MIG-001 — function is a NO-OP when the legacy table doesn't exist."""
    from sqlalchemy import inspect
    from app.db.models import Base
    from app.db.session import (
        engine_for_path,
        migrate_subscriptions_to_category_subscriptions,
    )

    db_path = tmp_path / "mig_noop.sqlite"
    eng = engine_for_path(str(db_path))
    Base.metadata.create_all(eng)
    try:
        n = migrate_subscriptions_to_category_subscriptions(eng)
        assert n == 0
        # Calling twice is still a NO-OP.
        n2 = migrate_subscriptions_to_category_subscriptions(eng)
        assert n2 == 0
        names = set(inspect(eng).get_table_names())
        assert "subscriptions" not in names
        assert "category_subscriptions" in names
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()


def test_migration_subscriptions_renamed_idempotent(tmp_path):
    """FR-MIG-001 — legacy subscriptions rows are copied into the new table once."""
    from sqlalchemy import inspect, text
    from app.db.models import Base
    from app.db.session import (
        engine_for_path,
        migrate_subscriptions_to_category_subscriptions,
    )

    db_path = tmp_path / "mig_copy.sqlite"
    eng = engine_for_path(str(db_path))
    Base.metadata.create_all(eng)
    try:
        # Bootstrap minimal FK dependencies (users + categories) and the
        # legacy `subscriptions` table with two pre-existing rows.
        with eng.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (id, name, dept, email, role) "
                "VALUES ('u1', 'op', 'AX', 'op@x', 'operator')"
            ))
            conn.execute(text(
                "INSERT INTO categories (id, name, owner_dept) "
                "VALUES ('reg', '금융 규제', 'comp'),"
                "('ai', 'AI', 'tech')"
            ))
            conn.execute(text(
                "CREATE TABLE subscriptions ("
                "id VARCHAR(32) PRIMARY KEY, user_id VARCHAR(32), "
                "category_id VARCHAR(32), subscribed BOOLEAN, "
                "channel VARCHAR(8), updated_at DATETIME)"
            ))
            conn.execute(text(
                "INSERT INTO subscriptions "
                "(id, user_id, category_id, subscribed, channel, updated_at) "
                "VALUES "
                "('s1id', 'u1', 'reg', 1, 'instant', '2026-05-10T00:00:00+00:00'),"
                "('s2id', 'u1', 'ai',  0, 'off',     '2026-05-10T00:00:00+00:00')"
            ))

        # Migrate — should copy 2 rows and drop the legacy table.
        n = migrate_subscriptions_to_category_subscriptions(eng)
        assert n == 2

        names = set(inspect(eng).get_table_names())
        assert "subscriptions" not in names
        assert "category_subscriptions" in names

        with eng.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, user_id, category_id, subscribed, channel "
                "FROM category_subscriptions ORDER BY id"
            )).all()
        # SQLite returns BOOLEAN as int; coerce for comparison.
        coerced = [(r[0], r[1], r[2], bool(r[3]), r[4]) for r in rows]
        assert coerced == [
            ("s1id", "u1", "reg", True, "instant"),
            ("s2id", "u1", "ai", False, "off"),
        ]

        # Idempotent — running again is a NO-OP.
        n2 = migrate_subscriptions_to_category_subscriptions(eng)
        assert n2 == 0
    finally:
        Base.metadata.drop_all(eng)
        eng.dispose()
