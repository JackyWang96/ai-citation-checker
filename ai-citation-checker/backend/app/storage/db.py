import aiosqlite
from datetime import datetime, timezone, timedelta

DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    filename    TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    expires_at  TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_expires_at ON reports(expires_at);

CREATE TABLE IF NOT EXISTS verified_references (
    id                      TEXT PRIMARY KEY,
    doi                     TEXT UNIQUE,
    title_normalized        TEXT NOT NULL,
    first_author_normalized TEXT NOT NULL,
    year                    INTEGER NOT NULL,
    canonical_json          TEXT NOT NULL,
    source                  TEXT NOT NULL,
    cached_at               TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vr_doi ON verified_references(doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vr_lookup ON verified_references(
    title_normalized, first_author_normalized, year
);
"""


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(DDL)
        await db.commit()


async def save_report(db_path: str, report_id: str, report_json: str, filename: str) -> None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=24)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?)",
            (report_id, report_json, filename, now.isoformat(), expires.isoformat()),
        )
        await db.commit()


async def get_report(db_path: str, report_id: str) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM reports WHERE id=? AND expires_at > ?",
            (report_id, now),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_cached_reference(db_path: str, doi: str | None,
                                title_norm: str, author_norm: str, year: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if doi:
            async with db.execute(
                "SELECT * FROM verified_references WHERE doi=?", (doi,)
            ) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT * FROM verified_references WHERE title_normalized=? "
                "AND first_author_normalized=? AND year=?",
                (title_norm, author_norm, year),
            ) as cur:
                row = await cur.fetchone()
    return dict(row) if row else None


async def save_verified_reference(db_path: str, ref_id: str, doi: str | None,
                                   title_norm: str, author_norm: str, year: int,
                                   canonical_json: str, source: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO verified_references
               VALUES (?,?,?,?,?,?,?,?)""",
            (ref_id, doi, title_norm, author_norm, year, canonical_json, source, now),
        )
        await db.commit()
