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


def _make_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        await init_db(cfg.DB_PATH)
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
