"""Step 4 — Watchtower NotifierService unit tests.

External SMTP is replaced with a stub class everywhere; ``time.sleep`` is
swapped for a no-op so retry/backoff branches don't slow the suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

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
from monitor.watchtower.notifier import (
    EMAIL_RETRY_BACKOFFS_SEC,
    NotifierService,
    RATE_LIMIT_MAX,
)
from tests.conftest import enable_all_site_subscriptions


# ---------------------------------------------------------------------------
# SMTP stub
# ---------------------------------------------------------------------------


class _SmtpStub:
    """Recording stand-in for ``smtplib.SMTP`` — drives all notifier paths."""

    instances: list["_SmtpStub"] = []

    def __init__(self, *, fail_starttls: bool = False, fail_send: bool = False,
                 send_attempts_until_success: int | None = None) -> None:
        self.fail_starttls = fail_starttls
        self.fail_send = fail_send
        # If set: the Nth and later send attempts succeed (1-indexed).
        self.send_attempts_until_success = send_attempts_until_success
        self.calls: list[tuple[str, tuple, dict]] = []
        self.send_count = 0
        type(self).instances.append(self)

    # smtplib API surface used by NotifierService._send_one
    def __call__(self, *args, **kwargs) -> "_SmtpStub":
        # Allow `_SmtpStub` to be used as a factory: SmtpFactory(server, port, timeout=...)
        self.calls.append(("ctor", args, kwargs))
        return self

    def __enter__(self) -> "_SmtpStub":
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def ehlo(self) -> None:
        self.calls.append(("ehlo", (), {}))

    def starttls(self) -> None:
        self.calls.append(("starttls", (), {}))
        if self.fail_starttls:
            raise RuntimeError("starttls denied by stub")

    def login(self, user: str, password: str) -> None:
        self.calls.append(("login", (user, "***"), {}))

    def send_message(self, msg: Any) -> None:
        self.send_count += 1
        if self.send_attempts_until_success is not None:
            if self.send_count < self.send_attempts_until_success:
                raise OSError(f"transient failure {self.send_count}")
        elif self.fail_send:
            raise OSError("permanent send failure")
        self.calls.append(("send_message", (str(msg.get("Subject", "")),), {}))

    def quit(self) -> None:
        self.calls.append(("quit", (), {}))


def _make_factory(stub: _SmtpStub):
    def _factory(server, port, timeout):
        stub.calls.append(("ctor", (server, port), {"timeout": timeout}))
        return stub
    return _factory


_SMTP_OK = {
    "server": "smtp.local",
    "port": 587,
    "user": "ops@example.com",
    "password": "x",
    "from_email": "watchtower@example.com",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(watchtower_db):
    """Yield a sessionmaker with the standard seed loaded.

    Step 4.5 — also flips every SiteSubscription to enabled=True for u1 so
    the AND filter (FR-NOTIF-009) doesn't suppress mails for sites that the
    YAML seed left at enabled=False. Tests that need to exercise the
    site-level OFF gate flip individual rows back manually.
    """
    with watchtower_db() as session:
        run_seed(session)
        # Also force every Site.enabled=True so YAML-disabled sites (s1/s4/s5/…)
        # don't block AND-filter tests; per-test cases that need a system
        # disabled site flip individual rows back.
        for site in session.query(Site).all():
            site.enabled = True
        session.commit()
        enable_all_site_subscriptions(session, user_id="u1")
    return watchtower_db


def _add_item(sm, *, site_id: str = "s2", iid: str = "iN1",
              title: str = "신규 공지", read_by: str = "",
              detected: datetime | None = None) -> str:
    detected = detected or datetime.now(timezone.utc)
    with sm() as session:
        session.add(Item(
            id=iid, site_id=site_id, type="NEW", title=title, summary="요약",
            url=f"https://example.com/{iid}", content_hash="h" * 64,
            detected_at=detected, read_by=read_by,
        ))
        session.commit()
    return iid


def _subscribe(sm, *, user_id: str = "u1", category_id: str, channel: str) -> None:
    with sm() as session:
        existing = session.query(CategorySubscription).filter_by(
            user_id=user_id, category_id=category_id
        ).first()
        if existing is None:
            session.add(CategorySubscription(
                user_id=user_id, category_id=category_id,
                subscribed=True, channel=channel,
            ))
        else:
            existing.subscribed = True
            existing.channel = channel
        session.commit()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def test_subscription_and_alertlog_models(watchtower_db):
    """Models accept defaults and round-trip through the ORM."""
    with watchtower_db() as session:
        # Minimal fixtures so we don't collide with the seed's default
        # subscription matrix (run_seed pre-fills u1×8 categories).
        session.add(User(id="m_u", name="x", dept="x", email="m@example.com"))
        session.add(Category(id="m_cat", name="x", owner_dept="x"))
        session.commit()

        sub = CategorySubscription(user_id="m_u", category_id="m_cat",
                                   subscribed=True, channel="instant")
        session.add(sub)
        session.commit()
        assert sub.id and len(sub.id) == 32
        assert sub.updated_at is not None

        log = AlertLog(
            user_id="m_u", item_id=None, channel="digest",
            sent_at=datetime.now(timezone.utc), status="skipped",
            detail="0 items in 0 categories",
        )
        session.add(log)
        session.commit()
        assert log.id and len(log.id) == 32


# ---------------------------------------------------------------------------
# send_instant
# ---------------------------------------------------------------------------


def test_notifier_send_instant_no_smtp(seeded):
    """SMTP not configured → status='skipped' rows."""
    _subscribe(seeded, category_id="reg", channel="instant")
    iid = _add_item(seeded, site_id="s2", iid="iI1")

    notifier = NotifierService(seeded, smtp_config={}, sleep=lambda _: None)
    res = notifier.send_instant([iid])

    assert res["sent"] == 0
    assert res["skipped"] == 1
    with seeded() as session:
        rows = session.query(AlertLog).filter_by(channel="instant").all()
        assert len(rows) == 1
        assert rows[0].status == "skipped"
        assert rows[0].error_message == "SMTP not configured"
        assert rows[0].item_id == iid


def test_notifier_send_instant_with_smtp(seeded):
    """SMTP configured + happy path → status='sent'."""
    _subscribe(seeded, category_id="reg", channel="instant")
    iid = _add_item(seeded, site_id="s2", iid="iI2")

    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    res = notifier.send_instant([iid])

    assert res == {"sent": 1, "failed": 0, "skipped": 0, "rolled_up": 0}
    assert any(c[0] == "send_message" for c in stub.calls)
    with seeded() as session:
        rows = session.query(AlertLog).filter_by(channel="instant").all()
        assert len(rows) == 1
        assert rows[0].status == "sent"


def test_notifier_send_instant_smtp_failure_with_backoff(seeded, monkeypatch):
    """Permanent SMTP failure → status='failed' after retries."""
    _subscribe(seeded, category_id="reg", channel="instant")
    iid = _add_item(seeded, site_id="s2", iid="iI3")

    sleeps: list[float] = []
    stub = _SmtpStub(fail_send=True)
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda s: sleeps.append(s),
        smtp_factory=_make_factory(stub),
    )
    res = notifier.send_instant([iid])

    assert res["sent"] == 0
    assert res["failed"] == 1
    # 3 attempts → 2 sleeps (between attempts).
    assert len(sleeps) == len(EMAIL_RETRY_BACKOFFS_SEC) - 1
    assert sleeps == list(EMAIL_RETRY_BACKOFFS_SEC[:-1])
    with seeded() as session:
        row = session.query(AlertLog).filter_by(channel="instant").one()
        assert row.status == "failed"
        assert row.error_message  # non-empty


def test_notifier_send_instant_rate_limit_rollup(seeded):
    """11th instant message inside the window collapses into a digest log row."""
    _subscribe(seeded, category_id="reg", channel="instant")
    item_ids = [
        _add_item(seeded, site_id="s2", iid=f"iRL{i}", title=f"t{i}")
        for i in range(RATE_LIMIT_MAX + 1)
    ]

    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    res = notifier.send_instant(item_ids)

    assert res["sent"] == RATE_LIMIT_MAX
    assert res["rolled_up"] == 1

    with seeded() as session:
        sent_rows = session.query(AlertLog).filter_by(
            channel="instant", status="sent"
        ).count()
        digest_rows = session.query(AlertLog).filter_by(
            channel="digest", status="skipped"
        ).count()
        assert sent_rows == RATE_LIMIT_MAX
        assert digest_rows == 1


# ---------------------------------------------------------------------------
# send_digest
# ---------------------------------------------------------------------------


def test_notifier_send_digest_groups_by_category(seeded):
    """Digest groups items by category and reports detail summary."""
    _subscribe(seeded, category_id="reg", channel="digest")
    _subscribe(seeded, category_id="ai", channel="digest")
    _add_item(seeded, site_id="s2", iid="dR1", title="reg-1")
    _add_item(seeded, site_id="s17", iid="dA1", title="ai-1")
    _add_item(seeded, site_id="s17", iid="dA2", title="ai-2")

    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    res = notifier.send_digest()

    assert res["sent"] == 1
    assert res["users"] == 1
    with seeded() as session:
        row = session.query(AlertLog).filter_by(channel="digest").one()
        assert row.status == "sent"
        # detail format: "{N} items in {M} categories"
        assert "3 items in 2 categories" in (row.detail or "")


def test_notifier_send_digest_excludes_read(seeded):
    """Items already read by the recipient are excluded (FR-NOTIF-008)."""
    _subscribe(seeded, category_id="reg", channel="digest")
    # u1 has already read this item.
    _add_item(seeded, site_id="s2", iid="dRD", title="already read", read_by="u1")
    _add_item(seeded, site_id="s2", iid="dRE", title="fresh")

    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    notifier.send_digest()

    with seeded() as session:
        row = session.query(AlertLog).filter_by(channel="digest").one()
        assert "1 items in 1 categories" in (row.detail or "")


def test_notifier_send_digest_no_items_skipped(seeded):
    """No fresh items in the 24h window → status='skipped'."""
    _subscribe(seeded, category_id="reg", channel="digest")
    # Outside the 24h window
    _add_item(seeded, site_id="s2", iid="dOLD",
              detected=datetime.now(timezone.utc) - timedelta(days=2))

    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    res = notifier.send_digest()
    assert res["sent"] == 0
    assert res["skipped"] == 1


# ---------------------------------------------------------------------------
# send_owner_failure
# ---------------------------------------------------------------------------


def test_notifier_send_owner_failure(seeded):
    """5회 실패 site → category owner mail with consecutive count."""
    # Wire u1 as owner of category 'reg'.
    with seeded() as session:
        cat = session.get(Category, "reg")
        cat.owner_user_id = "u1"
        session.commit()

    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    res = notifier.send_owner_failure("s2", 5)

    assert res["sent"] == 1
    with seeded() as session:
        row = session.query(AlertLog).filter_by(channel="owner_failure").one()
        assert row.status == "sent"
        assert "site=s2" in (row.detail or "")
        assert "streak=5" in (row.detail or "")


def test_notifier_send_owner_failure_no_email_skipped(seeded):
    """Category with no owner_user_id → status='skipped' + no alert_log row.

    AlertLog.user_id is FK-bound to users.id, so an unresolvable owner
    cannot satisfy the constraint. The notifier returns skipped without
    pollution; an owner with a user but no email persists a skipped row.
    """
    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    res = notifier.send_owner_failure("s2", 5)
    assert res["sent"] == 0
    assert res["skipped"] == 1
    assert res["reason"] in {"no_owner_user", "no_owner_email"}
    with seeded() as session:
        # No AlertLog row written when owner cannot be resolved at all.
        rows = session.query(AlertLog).filter_by(channel="owner_failure").all()
        assert rows == []

    # When the owner is wired but has no email, expect a skipped row.
    with seeded() as session:
        cat = session.get(Category, "reg")
        cat.owner_user_id = "u1"
        u1 = session.get(User, "u1")
        u1.email = ""
        session.commit()

    res2 = notifier.send_owner_failure("s2", 5)
    assert res2["skipped"] == 1
    with seeded() as session:
        row = session.query(AlertLog).filter_by(channel="owner_failure").one()
        assert row.status == "skipped"
        assert row.error_message == "owner has no email"


def _disable_site_for_user(sm, *, user_id: str = "u1", site_id: str) -> None:
    """Test helper — flip (user_id, site_id) SiteSubscription to enabled=False."""
    with sm() as session:
        row = session.query(SiteSubscription).filter_by(
            user_id=user_id, site_id=site_id,
        ).first()
        if row is None:
            session.add(SiteSubscription(
                user_id=user_id, site_id=site_id, enabled=False,
            ))
        else:
            row.enabled = False
        session.commit()


# ---------------------------------------------------------------------------
# Step 4.5 — AND filter (FR-NOTIF-009)
# ---------------------------------------------------------------------------


def test_send_instant_blocked_when_site_subscription_off(seeded):
    """FR-NOTIF-009 — site OFF + category ON → no notification, no AlertLog row."""
    _subscribe(seeded, category_id="reg", channel="instant")
    _disable_site_for_user(seeded, site_id="s2")
    iid = _add_item(seeded, site_id="s2", iid="iAND1")

    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    res = notifier.send_instant([iid])

    assert res == {"sent": 0, "failed": 0, "skipped": 0, "rolled_up": 0}
    with seeded() as session:
        rows = session.query(AlertLog).filter_by(channel="instant").all()
        # Site-OFF is treated as "no subscriber" — no log entry.
        assert rows == []


def test_send_instant_blocked_when_system_disabled_site(seeded):
    """FR-NOTIF-009 — Site.enabled=False blocks the notification entirely."""
    _subscribe(seeded, category_id="reg", channel="instant")
    with seeded() as session:
        s2 = session.get(Site, "s2")
        s2.enabled = False
        session.commit()
    iid = _add_item(seeded, site_id="s2", iid="iAND2")

    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    res = notifier.send_instant([iid])

    assert res["sent"] == 0
    with seeded() as session:
        assert session.query(AlertLog).filter_by(channel="instant").count() == 0


def test_send_digest_filters_by_site_enabled(seeded):
    """FR-NOTIF-009 — digest only counts items whose user has the site ON."""
    _subscribe(seeded, category_id="ai", channel="digest")
    # User opts out of s17 specifically.
    _disable_site_for_user(seeded, site_id="s17")
    _add_item(seeded, site_id="s17", iid="dAND1", title="s17 item (suppressed)")
    _add_item(seeded, site_id="s20", iid="dAND2", title="s20 item (delivered)")

    stub = _SmtpStub()
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda _: None, smtp_factory=_make_factory(stub),
    )
    notifier.send_digest()

    with seeded() as session:
        row = session.query(AlertLog).filter_by(channel="digest").one()
        # 1 item (s20) survives the AND filter; s17 is suppressed.
        assert "1 items in 1 categories" in (row.detail or "")


def test_notifier_starttls_fail_closed(seeded):
    """STARTTLS failure → status='failed' and no plaintext fallback."""
    _subscribe(seeded, category_id="reg", channel="instant")
    iid = _add_item(seeded, site_id="s2", iid="iSTL")

    stub = _SmtpStub(fail_starttls=True)
    sleeps: list[float] = []
    notifier = NotifierService(
        seeded, smtp_config=_SMTP_OK,
        sleep=lambda s: sleeps.append(s),
        smtp_factory=_make_factory(stub),
    )
    res = notifier.send_instant([iid])
    assert res["failed"] == 1
    # No retries on STARTTLS failures.
    assert sleeps == []
    with seeded() as session:
        row = session.query(AlertLog).filter_by(channel="instant").one()
        assert row.status == "failed"
        assert row.error_message == "STARTTLS failed"
