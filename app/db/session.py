"""SQLAlchemy engine, session factory, and DB initialization.

Watchtower uses sync SQLAlchemy 2.x (Decision §1). Async DB (aiosqlite) is
deferred to Step 5 — for Phase 1 this keeps dependencies minimal and
matches the FastAPI BackgroundTasks usage already in `app/routes/status.py`.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

logger = logging.getLogger(__name__)

# Default DB path under the worktree's `data/` dir. Tests override with
# `engine_for_path()` so they never touch this module-level engine.
_BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB_PATH = _BASE_DIR / "data" / "watchtower.sqlite"


def _apply_pragmas(dbapi_connection, _connection_record) -> None:
    """Per-connection PRAGMA setup (Decision §2 — WAL + FK enforcement)."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _build_engine(db_path: str) -> Engine:
    """Build a SQLite engine pinned to `db_path` with WAL + FK PRAGMAs."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(
        f"sqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    event.listen(eng, "connect", _apply_pragmas)
    return eng


# Module-level engine for production use.
engine: Engine = _build_engine(str(_DEFAULT_DB_PATH))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Test helpers — fixtures inject a tmp_path-backed engine via these factories.
# ---------------------------------------------------------------------------


def engine_for_path(db_path: str) -> Engine:
    """Build an isolated engine bound to `db_path` (used by test fixtures)."""
    return _build_engine(db_path)


def sessionmaker_for_engine(eng: Engine):
    """Return a sessionmaker bound to a custom engine (test fixtures)."""
    return sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create all tables, run one-shot migrations, and lock down DB perms.

    Called once during FastAPI lifespan startup. Idempotent: SQLAlchemy's
    `create_all` is a no-op when tables already exist.
    """
    Base.metadata.create_all(engine)
    migrate_subscriptions_to_category_subscriptions(engine)
    # NFR-SEC-005 / Constraints — file permissions 0o600. Best-effort: on
    # Windows `chmod` is a no-op (permissions model differs). Same policy as
    # Step 1 JSONL files.
    db_path = _DEFAULT_DB_PATH
    if db_path.exists():
        try:
            os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as exc:
            logger.debug("chmod 0o600 skipped for %s: %s", db_path, exc)


def migrate_subscriptions_to_category_subscriptions(eng: Engine) -> int:
    """FR-MIG-001 — copy rows from the legacy `subscriptions` table.

    Runs once on every boot but is idempotent: when the legacy table is
    absent or the new table already has rows the function exits with a
    NO-OP. Returns the count of rows transferred (0 when nothing to do).

    Strategy: single transaction — INSERT…SELECT into
    `category_subscriptions`, then DROP the legacy table. On any failure
    the transaction rolls back and we log a WARN; boot is *not* aborted
    so the operator can recover manually.
    """
    insp = inspect(eng)
    if "subscriptions" not in insp.get_table_names():
        logger.info("[migration] subscriptions table not found — skip")
        return 0

    with eng.begin() as conn:
        existing = conn.execute(
            text("SELECT COUNT(*) FROM category_subscriptions")
        ).scalar_one()
        if existing:
            # Already migrated (or independently seeded). Drop the legacy
            # table to avoid drifting back into the old schema.
            conn.execute(text("DROP TABLE subscriptions"))
            logger.info(
                "[migration] subscriptions dropped — category_subscriptions "
                "already populated (%d rows)",
                existing,
            )
            return 0

        try:
            result = conn.execute(
                text(
                    "INSERT INTO category_subscriptions "
                    "(id, user_id, category_id, subscribed, channel, updated_at) "
                    "SELECT id, user_id, category_id, subscribed, channel, updated_at "
                    "FROM subscriptions"
                )
            )
            transferred = result.rowcount or 0
            conn.execute(text("DROP TABLE subscriptions"))
            logger.info(
                "[migration] subscriptions → category_subscriptions: "
                "%d rows migrated",
                transferred,
            )
            return transferred
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "[migration] failed: %s; continuing with current schema",
                type(exc).__name__,
            )
            raise


def get_session() -> Iterator[Session]:
    """FastAPI dependency — yields a session, ensures close on exit."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
