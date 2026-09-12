import logging
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import app.config as cfg
from app.storage.db import init_db
from app.storage.cleanup import start_cleanup_scheduler
from app.routes.upload import router as upload_router
from app.routes.report import router as report_router
from app.routes.analyze import router as analyze_router
from app.services import rules_retriever

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent.parent.parent / "frontend" / "dist"


def _git_sha() -> str:
    sha = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_SHA")
    if sha:
        return sha[:7]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent.parent.parent,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


GIT_SHA = _git_sha()

limiter = Limiter(key_func=get_remote_address, default_limits=["10/minute"])


def _check_rules_index() -> None:
    """Validate the APA rules index at boot and disable RAG if it is unusable.

    Deliberately not fail-fast. A stale or missing index is a deployment
    mistake and deserves a loud error, but it is a mistake in one optional
    feature — refusing to boot would take document checking, uploads and
    reports down with it, none of which touch the index. This also matches
    what the retrieval layer already does at request time.

    The error is logged at ERROR level and RAG_ENABLED is cleared, so
    suggestions fall back to their pre-RAG quality rather than being built on
    rules that cannot be trusted.
    """
    if not cfg.RAG_ENABLED:
        return
    try:
        rules_retriever.open_index()
        logger.info("APA rules index verified")
    except Exception as exc:
        cfg.RAG_ENABLED = False
        logger.error(
            "APA rules index unusable; AI fix suggestions will run without "
            "rule guidance. Rebuild with `python -m app.scripts.build_rules_index`. %s",
            exc,
        )


def _make_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await init_db(cfg.DB_PATH)
        _check_rules_index()
        scheduler = start_cleanup_scheduler()
        yield
        scheduler.shutdown()

    application = FastAPI(lifespan=lifespan)
    application.state.limiter = limiter
    application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[cfg.ALLOWED_ORIGIN],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    application.include_router(upload_router)
    application.include_router(report_router)
    application.include_router(analyze_router)

    @application.get("/healthz", include_in_schema=False)
    async def healthz():
        return JSONResponse({"status": "ok", "version": GIT_SHA})

    @application.get("/version", include_in_schema=False)
    async def version():
        return JSONResponse({"version": GIT_SHA})

    if STATIC_DIR.exists():
        application.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @application.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(_: str):
            return FileResponse(STATIC_DIR / "index.html")

    return application


async def create_app() -> FastAPI:
    """Async factory used in tests."""
    return _make_app()


# Module-level app for uvicorn
app = _make_app()
