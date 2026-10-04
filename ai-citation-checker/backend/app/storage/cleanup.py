import aiosqlite
import app.config as cfg
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.storage.db import delete_stale_claims


async def delete_expired_reports() -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(cfg.DB_PATH) as db:
        await db.execute("DELETE FROM reports WHERE expires_at < ?", (now,))
        await db.commit()
    # After the reports go, so their leases are caught as orphans.
    await delete_stale_claims(cfg.DB_PATH)


def start_cleanup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(delete_expired_reports, "interval", hours=1)
    scheduler.start()
    return scheduler
