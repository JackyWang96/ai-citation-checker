import json

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

-- Leases taken before a citation is sent to the LLM. Two browser tabs hitting
-- /api/analyze at once would otherwise both read it as unanalysed and both
-- pay. The primary key makes the claim atomic.
--
-- `done` is a tombstone. A finished citation keeps its row, owner intact,
-- until the report itself goes. Deleting it on completion erased the record
-- that anyone else had worked on the citation, so a stale worker whose lease
-- had been taken over saw no other owner and wrote over the newer result.
CREATE TABLE IF NOT EXISTS citation_claims (
    report_id   TEXT NOT NULL,
    citation_id TEXT NOT NULL,
    owner       TEXT NOT NULL,
    expires_at  TIMESTAMP NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (report_id, citation_id)
);
CREATE INDEX IF NOT EXISTS idx_claims_expires_at ON citation_claims(expires_at);

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
        # CREATE TABLE IF NOT EXISTS never adds a column to a table that is
        # already there, so a database created before the tombstone existed
        # would keep running without it — and every claim would then fail.
        #
        # Under a write lock, because Railway starts four workers and each runs
        # this at boot. Checked outside a lock, two could both see the column
        # missing and the second ALTER would fail with "duplicate column" —
        # measured at 7 failures in 100 worker starts with real processes.
        # The worker that gets the lock second re-reads the schema and skips.
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute("PRAGMA table_info(citation_claims)")
        columns = {row[1] for row in await cur.fetchall()}
        if "done" not in columns:
            await db.execute(
                "ALTER TABLE citation_claims "
                "ADD COLUMN done INTEGER NOT NULL DEFAULT 0"
            )
        await db.commit()


async def save_report(db_path: str, report_id: str, report_json: str, filename: str) -> None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=24)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO reports (id, report_json, filename, created_at, expires_at) VALUES (?,?,?,?,?)",
            (report_id, report_json, filename, now.isoformat(), expires.isoformat()),
        )
        await db.commit()


async def claim_citations(
    db_path: str,
    report_id: str,
    citation_ids: list[str],
    owner: str,
    ttl_seconds: int,
) -> set[str]:
    """Take a lease on each citation, returning only the ones this caller won.

    The caller must analyse nothing it did not win. Leases expire, so a worker
    that crashes mid-analysis does not strand its citations; finished ones
    (`done`) never expire and can never be claimed again.

    "Won" means this call's INSERT actually landed. An earlier version re-read
    rows by owner and filtered on `expires_at > now`, but `now` was the
    transaction's own start time, so every freshly inserted row passed by
    construction and the filter decided nothing.
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        # Timed after the write lock is held: computing it before would let a
        # long lock wait silently eat into the lease.
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        # Only abandoned in-flight leases are reclaimable. Tombstones stay.
        await db.execute(
            "DELETE FROM citation_claims "
            "WHERE report_id=? AND done=0 AND expires_at<=?",
            (report_id, now.isoformat()),
        )
        won: set[str] = set()
        for citation_id in citation_ids:
            cur = await db.execute(
                "INSERT OR IGNORE INTO citation_claims "
                "(report_id, citation_id, owner, expires_at) VALUES (?,?,?,?)",
                (report_id, citation_id, owner, expires),
            )
            if cur.rowcount == 1:
                won.add(citation_id)
        await db.commit()
    return won


async def release_claims(
    db_path: str,
    report_id: str,
    owner: str,
    citation_ids: list[str] | None = None,
) -> None:
    """Drop this owner's in-flight leases — all of them, or just
    `citation_ids` — so a failed attempt can be retried at once instead of
    waiting out the TTL. Tombstones are never released."""
    async with aiosqlite.connect(db_path) as db:
        if citation_ids is None:
            await db.execute(
                "DELETE FROM citation_claims "
                "WHERE report_id=? AND owner=? AND done=0",
                (report_id, owner),
            )
        else:
            await db.executemany(
                "DELETE FROM citation_claims "
                "WHERE report_id=? AND owner=? AND citation_id=? AND done=0",
                [(report_id, owner, cid) for cid in citation_ids],
            )
        await db.commit()


async def delete_stale_claims(db_path: str) -> None:
    """Remove abandoned in-flight leases, and every row whose report is gone.

    Expired rows were otherwise only cleared when the same report was claimed
    again, so a worker killed mid-analysis on a report nobody reopened left its
    rows behind indefinitely. Tombstones are kept for as long as their report
    exists — they are what stops a stale worker overwriting a finished result.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM citation_claims WHERE done=0 AND expires_at<=?", (now,)
        )
        await db.execute(
            "DELETE FROM citation_claims "
            "WHERE report_id NOT IN (SELECT id FROM reports)"
        )
        await db.commit()


async def merge_citation_fields(
    db_path: str,
    report_id: str,
    updates: dict[str, dict],
    owner: str | None = None,
) -> dict | None:
    """Apply per-citation fields to a stored report, inside one transaction.

    Read-modify-write over the whole blob loses data: two analyse requests both
    read the unanalysed report, and whichever writes last replaces the other's
    results. The overwritten citation goes back to looking unanalysed, so it
    gets paid for again. Re-reading under the write lock means a concurrent
    writer's fields survive.

    Merging is monotonic per citation: a stored suggestion is never replaced by
    a weaker outcome. Serialising the writers is not enough on its own — two
    requests analysing the *same* citation can disagree, and if the one that
    produced no fix commits second it would erase a suggestion that was already
    paid for.

    When `owner` is given, a citation is skipped if any row for it belongs to
    someone else — in flight, abandoned, or finished. That last case is why
    finished rows are kept as tombstones: once a successor completed and its
    row was deleted, a stale worker saw no other owner and wrote over the newer
    result. A citation with no row at all, or only this owner's, is written:
    an analysis that outlived its own lease with nobody taking over has still
    been paid for, and discarding it would only mean paying again.

    Written citations become tombstones; this owner's other in-flight leases
    (claimed but produced nothing) are released so they stay retryable.

    Returns the merged report, or None if the report is gone.
    """
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            "SELECT report_json FROM reports WHERE id=?", (report_id,)
        )
        row = await cur.fetchone()
        if row is None:
            await db.rollback()
            return None

        if owner is not None:
            cur = await db.execute(
                "SELECT citation_id FROM citation_claims "
                "WHERE report_id=? AND owner<>?",
                (report_id, owner),
            )
            someone_else = {r[0] for r in await cur.fetchall()}
            updates = {k: v for k, v in updates.items() if k not in someone_else}

        report = json.loads(row["report_json"])
        # Tombstones go only to citations whose result actually landed. Taking
        # them from `updates` meant an empty or unknown entry left a done=1 row
        # with nothing written — a citation blocked from ever being retried.
        applied: list[str] = []
        for citation in report.get("citations", []):
            fields = updates.get(citation["id"])
            if not fields:
                continue
            if citation.get("suggestion") and not fields.get("suggestion"):
                continue   # a decline must not overwrite an existing fix
            citation.update(fields)
            applied.append(citation["id"])

        await db.execute(
            "UPDATE reports SET report_json=? WHERE id=?",
            (json.dumps(report), report_id),
        )
        if owner is not None:
            await db.executemany(
                "INSERT INTO citation_claims "
                "(report_id, citation_id, owner, expires_at, done) "
                "VALUES (?,?,?,?,1) "
                "ON CONFLICT(report_id, citation_id) DO UPDATE SET done=1 "
                "WHERE owner=excluded.owner",
                [(report_id, cid, owner, "9999-12-31T00:00:00+00:00")
                 for cid in applied],
            )
            await db.execute(
                "DELETE FROM citation_claims "
                "WHERE report_id=? AND owner=? AND done=0",
                (report_id, owner),
            )
        await db.commit()
        return report


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
               (id, doi, title_normalized, first_author_normalized, year,
                canonical_json, source, cached_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ref_id, doi, title_norm, author_norm, year, canonical_json, source, now),
        )
        await db.commit()
