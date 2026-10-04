import json

import aiosqlite

import app.config as cfg
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
    -- Charged attempts this citation has consumed in this report. Taken at
    -- claim time, before any money is spent, so a worker that dies after
    -- paying has still used its attempt. Refunded only when an attempt is
    -- known to have charged nothing. Survives release and lease expiry —
    -- which is why released rows are expired rather than deleted.
    attempts    INTEGER NOT NULL DEFAULT 1,
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
        if "attempts" not in columns:
            # Existing rows were each claimed once.
            await db.execute(
                "ALTER TABLE citation_claims "
                "ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1"
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
    max_attempts: int | None = None,
) -> set[str]:
    """Take a lease on each citation, returning only the ones this caller won.

    The caller must analyse nothing it did not win. A citation is won by
    inserting the first lease for it, or by taking over one that lapsed —
    and only while it still has charged attempts left. Winning consumes an
    attempt up front: the money is about to be spent, and a worker that dies
    after spending it must not get the attempt back.

    Finished citations (`done`) are never won again, and neither are ones
    whose budget is spent.

    "Won" means this call's own INSERT or UPDATE landed. An earlier version
    re-read rows by owner and filtered on `expires_at > now`, with `now` from
    the same transaction, so every fresh row passed and the filter decided
    nothing.
    """
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    if max_attempts is None:
        max_attempts = cfg.ANALYSIS_MAX_BILLED_ATTEMPTS
    async with aiosqlite.connect(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        # Timed after the write lock is held: computing it before would let a
        # long lock wait silently eat into the lease.
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        won: set[str] = set()
        for citation_id in citation_ids:
            cur = await db.execute(
                "INSERT OR IGNORE INTO citation_claims "
                "(report_id, citation_id, owner, expires_at, done, attempts) "
                "VALUES (?,?,?,?,0,1)",
                (report_id, citation_id, owner, expires),
            )
            if cur.rowcount != 1:
                # A row exists. Take it over only if it is an abandoned,
                # unfinished lease with budget left. Lapsed rows are kept
                # rather than deleted precisely so this count survives.
                cur = await db.execute(
                    "UPDATE citation_claims "
                    "SET owner=?, expires_at=?, attempts=attempts+1 "
                    "WHERE report_id=? AND citation_id=? AND done=0 "
                    "AND expires_at<=? AND attempts<?",
                    (owner, expires, report_id, citation_id,
                     now.isoformat(), max_attempts),
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
    refund: set[str] | None = None,
) -> None:
    """Hand back this owner's in-flight leases — all of them, or just
    `citation_ids` — so they can be retried at once rather than after the TTL.

    Released leases are expired, not deleted: deleting them would reset the
    attempt count, and with it the cap on what one citation can be charged.
    Citations in `refund` charged nothing on this attempt and get it back.
    Tombstones are never touched.
    """
    now = datetime.now(timezone.utc).isoformat()
    scope, args = "", []
    if citation_ids is not None:
        if not citation_ids:
            return
        scope = f" AND citation_id IN ({','.join('?' * len(citation_ids))})"
        args = list(citation_ids)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("BEGIN IMMEDIATE")
        if refund:
            await _refund(db, report_id, owner, refund)
        await db.execute(
            "UPDATE citation_claims SET expires_at=? "
            "WHERE report_id=? AND owner=? AND done=0" + scope,
            [now, report_id, owner, *args],
        )
        await db.commit()


async def _refund(db, report_id: str, owner: str, citation_ids: set[str]) -> None:
    """Return one attempt to each citation, never going below zero."""
    await db.executemany(
        "UPDATE citation_claims SET attempts=MAX(attempts-1, 0) "
        "WHERE report_id=? AND owner=? AND citation_id=? AND done=0",
        [(report_id, owner, cid) for cid in citation_ids],
    )


async def delete_stale_claims(db_path: str) -> None:
    """Remove every lease row whose report is gone.

    Rows are otherwise kept for the life of their report, lapsed ones
    included: a lapsed lease still carries the citation's attempt count, and
    dropping it would hand that citation a fresh budget. A tombstone, likewise,
    is what stops a stale worker overwriting a finished result.
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM citation_claims "
            "WHERE report_id NOT IN (SELECT id FROM reports)"
        )
        await db.commit()


_FAILED_FIELDS = {
    "suggestion": None,
    "suggestion_explanation": "",
    "suggestion_verified": False,
    "suggestion_rule_basis": [],
    "suggestion_validation": [],
    "suggestion_status": "failed",
}


def _mark_failed(report: dict, citation_ids: set[str]) -> None:
    """Record spent budgets on the report, so the citation reads as finished
    there too — otherwise the page keeps offering a button that can no longer
    win a claim and silently does nothing."""
    for citation in report.get("citations", []):
        if citation["id"] not in citation_ids:
            continue
        if citation.get("suggestion") or citation.get("suggestion_status"):
            continue   # already finished some other way; never downgrade it
        citation.update(_FAILED_FIELDS)


async def _settle_exhausted(db, report: dict, report_id: str, rows_sql: str,
                            args: list) -> None:
    """Mark lapsed, unfinished leases with no budget left as failed, and
    tombstone them. `rows_sql` narrows which rows are considered."""
    cur = await db.execute(
        "SELECT citation_id FROM citation_claims "
        "WHERE report_id=? AND done=0 AND attempts>=? AND expires_at<=?"
        + rows_sql,
        [report_id, cfg.ANALYSIS_MAX_BILLED_ATTEMPTS,
         datetime.now(timezone.utc).isoformat(), *args],
    )
    exhausted = {r[0] for r in await cur.fetchall()}
    if not exhausted:
        return
    _mark_failed(report, exhausted)
    await db.executemany(
        "UPDATE citation_claims SET done=1 WHERE report_id=? AND citation_id=?",
        [(report_id, cid) for cid in exhausted],
    )


async def fail_exhausted_citations(
    db_path: str, report_id: str, citation_ids: list[str]
) -> dict | None:
    """Settle citations whose budget ran out without anyone settling them.

    The normal path marks a citation failed when its last attempt settles.
    If the worker making that attempt died instead, nobody did: the lease
    lapses with the budget spent, no claim can win it again, and the report
    goes on showing it as analysable. Called for citations a request wanted
    but did not win. Returns the updated report, or None if unchanged.
    """
    if not citation_ids:
        return None
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
        report = json.loads(row["report_json"])
        before = json.dumps(report)
        await _settle_exhausted(
            db, report, report_id,
            f" AND citation_id IN ({','.join('?' * len(citation_ids))})",
            list(citation_ids),
        )
        if json.dumps(report) == before:
            await db.commit()
            return None
        await db.execute(
            "UPDATE reports SET report_json=? WHERE id=?",
            (json.dumps(report), report_id),
        )
        await db.commit()
        return report


async def merge_citation_fields(
    db_path: str,
    report_id: str,
    updates: dict[str, dict],
    owner: str | None = None,
    refund: set[str] | None = None,
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

    With an owner this is the settlement for everything the owner claimed,
    in one transaction: written citations become tombstones; those in
    `refund` charged nothing and get their attempt back; the rest are released
    for retry; and any whose charged attempts are now spent are marked failed
    on the report, so they stop being offered and stop being paid for.

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
            if refund:
                await _refund(db, report_id, owner, refund - set(applied))
            # Released by expiring, not deleting: the row keeps the attempt
            # count that caps what this citation can still be charged.
            await db.execute(
                "UPDATE citation_claims SET expires_at=? "
                "WHERE report_id=? AND owner=? AND done=0",
                (datetime.now(timezone.utc).isoformat(), report_id, owner),
            )
            await _settle_exhausted(db, report, report_id, " AND owner=?", [owner])

        # Written last: settling exhausted citations above edits the report.
        await db.execute(
            "UPDATE reports SET report_json=? WHERE id=?",
            (json.dumps(report), report_id),
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
