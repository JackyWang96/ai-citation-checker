from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import app.config as cfg
from app.storage.db import init_db
from app.storage.cleanup import start_cleanup_scheduler
from app.routes.upload import router as upload_router
from app.routes.report import router as report_router

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
    return application


async def create_app() -> FastAPI:
    """Async factory used in tests."""
    return _make_app()


# Module-level app for uvicorn
app = _make_app()
