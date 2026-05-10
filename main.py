"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uuid

import yaml
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from app.database import validate_jsonl_file
from app.db.import_legacy import import_legacy_events
from app.db.models import Site
from app.db.seed import run_seed
from app.db.session import SessionLocal, init_db
from app.routes.status import router as status_router
from app.routes.watchtower import router as watchtower_router
from app.scheduler import setup_scheduler
from monitor.watchtower.worker import WatchtowerWorker
from monitor.worker import Worker

# Paths
BASE_DIR = Path(__file__).resolve().parent
SOURCES_PATH = BASE_DIR / "config" / "sources.yaml"
KEYWORDS_PATH = BASE_DIR / "config" / "keywords.yaml"
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"

# Load .env early
load_dotenv(BASE_DIR / ".env")

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _load_sources_config() -> dict:
    if not SOURCES_PATH.exists():
        return {"sources": {}}
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"sources": {}}


def _build_smtp_config() -> dict:
    return {
        "server": os.getenv("SMTP_SERVER"),
        "port": os.getenv("SMTP_PORT"),
        "user": os.getenv("SMTP_USER"),
        "password": os.getenv("SMTP_PASSWORD"),
        "alert_email": os.getenv("ALERT_EMAIL"),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize Worker + scheduler on startup, shut down on exit."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # FR-12 / Case D / Edge B — repair any partial JSONL lines from a prior crash.
    for jsonl_name in ("events.jsonl", "alerts.jsonl"):
        path = str(DATA_DIR / jsonl_name)
        dropped = validate_jsonl_file(path)
        if dropped:
            logger.warning("Recovered %d corrupted line(s) from %s", dropped, path)

    # Watchtower (Step 2) — DB schema + idempotent seed + best-effort legacy import.
    init_db()
    with SessionLocal() as session:
        try:
            counts = run_seed(session)
            logger.info(
                "[seed] watchtower ready — categories=%d sites=%d users=%d",
                counts.get("categories", 0),
                counts.get("sites", 0),
                counts.get("users", 0),
            )
        except Exception as exc:
            logger.warning("Watchtower seed failed: %s", exc)
        try:
            imported = import_legacy_events(session, str(DATA_DIR / "events.jsonl"))
            if imported:
                logger.info("[legacy-import] %d events mapped to items", imported)
        except Exception as exc:
            logger.warning("Legacy import skipped: %s", exc)

    smtp_config = _build_smtp_config()
    worker = Worker(
        config_path=str(SOURCES_PATH),
        keywords_path=str(KEYWORDS_PATH),
        data_dir=str(DATA_DIR),
        smtp_config=smtp_config,
    )
    sources_config = _load_sources_config()
    scheduler = setup_scheduler(worker, sources_config)

    # Step 3 — Watchtower fleet scheduling (one job per enabled Site).
    watchtower_worker = WatchtowerWorker(SessionLocal)
    app.state.watchtower_worker = watchtower_worker

    registered = 0
    skipped = 0
    try:
        with SessionLocal() as session:
            sites = session.execute(select(Site).order_by(Site.id)).scalars().all()
            for site in sites:
                if not site.enabled:
                    skipped += 1
                    continue
                scheduler.add_job(
                    watchtower_worker.run_site,
                    "interval",
                    minutes=int(site.crawl_interval_min or 60),
                    args=[site.id],
                    id=f"watchtower_{site.id}",
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1,
                )
                registered += 1
        logger.info(
            "Watchtower scheduler: %d sites registered (%d skipped — disabled)",
            registered, skipped,
        )
    except Exception as exc:
        logger.warning("Watchtower scheduler setup failed: %s", exc)

    scheduler.start()

    app.state.worker = worker
    app.state.scheduler = scheduler
    logger.info("Application startup complete")

    try:
        yield
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception as exc:
            logger.warning("Scheduler shutdown error: %s", exc)
        try:
            watchtower_worker.shutdown(wait=False)
        except Exception as exc:
            logger.warning("Watchtower worker shutdown error: %s", exc)
        logger.info("Application shutdown complete")


app = FastAPI(title="claude_webcroll", version="0.1.0", lifespan=lifespan)
app.include_router(status_router)
app.include_router(watchtower_router)

# Static assets — Watchtower prototype + future bundled JS/CSS.
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root() -> dict[str, str]:
    return {"app": "claude_webcroll", "version": "0.1.0", "status": "ok"}


@app.get("/ui")
def ui() -> FileResponse:
    """Serve the Watchtower prototype UI (Step 2 entry point)."""
    return FileResponse(STATIC_DIR / "watchtower.html", media_type="text/html; charset=utf-8")


# ---------------------------------------------------------------------------
# Step 3 — Watchtower trigger endpoint
# ---------------------------------------------------------------------------


class WatchtowerTriggerBody(BaseModel):
    site_id: str | None = None


def _watchtower_run(worker: WatchtowerWorker, site_id: str | None) -> None:
    """BackgroundTasks shim — never raise out of a background task."""
    try:
        if site_id:
            worker.run_site(site_id)
        else:
            worker.run_all()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Watchtower trigger failed: %s", type(exc).__name__)


@app.post("/api/trigger-watchtower", status_code=202)
def trigger_watchtower(
    background_tasks: BackgroundTasks,
    body: WatchtowerTriggerBody | None = None,
    site_id: str | None = Query(default=None),
) -> JSONResponse:
    """Queue a Watchtower crawl pass and return 202 Accepted.

    Body and query parameter are both accepted for ``site_id``; an empty/
    omitted value triggers all enabled sites.
    """
    target = (body.site_id if body and body.site_id else None) or site_id
    job_id = uuid.uuid4().hex
    worker: WatchtowerWorker | None = getattr(app.state, "watchtower_worker", None)
    if worker is None:
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "site_id": target,
                "status": "queued",
                "message": "Trigger accepted (worker not ready — will be a no-op)",
            },
        )
    background_tasks.add_task(_watchtower_run, worker, target)
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "site_id": target,
            "status": "queued",
            "message": "Trigger accepted",
        },
    )
