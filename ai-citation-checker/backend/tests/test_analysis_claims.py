"""Leases that stop two concurrent analyse requests paying for the same work.

Railway runs four workers, so an in-process lock proves nothing — every
guarantee here has to hold through the database.
"""
from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

import pytest

import app.config as cfg
from app.storage.db import (
    claim_citations,
    get_report,
    init_db,
    merge_citation_fields,
    release_claims,
    save_report,
)

REPORT = {"citations": [
    {"id": "c1", "kind": "reference", "raw_text": "A",
     "issues": [{"type": "format_violation", "reason": "x"}]},
    {"id": "c2", "kind": "reference", "raw_text": "B",
     "issues": [{"type": "format_violation", "reason": "y"}]},
]}


def _migrate_in_child(path, barrier, results):
    """Module-level so multiprocessing can pickle it."""
    import asyncio as _asyncio
    barrier.wait()
    try:
        _asyncio.run(init_db(path))
        results.put("ok")
    except Exception as exc:   # noqa: BLE001 — report every failure to the parent
        results.put(repr(exc))


async def _expire(db_path: str, report_id: str, owner: str) -> None:
    """Push a lease into the past. claim_citations rejects a non-positive TTL
    (it would hand out a lease that is already dead), so an expired lease has
    to be produced the way it happens for real: a live one that ran out."""
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE citation_claims SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE report_id=? AND owner=?", (report_id, owner))
        await db.commit()


@pytest.fixture
async def db_path():
    path = str(Path(tempfile.mkdtemp()) / "reports.db")
    await init_db(path)
    await save_report(path, "rid", json.dumps(REPORT), "f.docx")
    return path


async def test_only_one_owner_wins_a_citation(db_path):
    """Whoever loses the claim must not analyse. This shows a live lease
    blocking a second claim; it is sequential, not a race between processes,
    and says nothing about a lease that has expired."""
    a = await claim_citations(db_path, "rid", ["c1", "c2"], "owner-a", 180)
    b = await claim_citations(db_path, "rid", ["c1", "c2"], "owner-b", 180)
    assert a == {"c1", "c2"}
    assert b == set(), "the second request must win nothing and pay nothing"


async def test_claims_are_scoped_to_their_report(db_path):
    await save_report(db_path, "other", json.dumps(REPORT), "g.docx")
    await claim_citations(db_path, "rid", ["c1"], "owner-a", 180)
    assert await claim_citations(db_path, "other", ["c1"], "owner-b", 180) == {"c1"}


async def test_an_expired_lease_is_taken_over(db_path):
    """A worker that dies mid-analysis must not strand its citations."""
    assert await claim_citations(db_path, "rid", ["c1"], "crashed", 180) == {"c1"}
    await _expire(db_path, "rid", "crashed")
    assert await claim_citations(db_path, "rid", ["c1"], "next", 180) == {"c1"}


async def test_a_stale_owner_cannot_overwrite_an_active_owner(db_path):
    """The reason the lease carries an owner at all. A slow worker whose lease
    expired and was taken over must not land its result on top of the worker
    now holding it."""
    await claim_citations(db_path, "rid", ["c1"], "slow", 180)
    await _expire(db_path, "rid", "slow")
    await claim_citations(db_path, "rid", ["c1"], "fresh", 180)

    # "fresh" is still analysing; "slow" finishes late and tries to write.
    await merge_citation_fields(db_path, "rid", {"c1": {"suggestion": "STALE"}},
                                owner="slow")
    stored = json.loads((await get_report(db_path, "rid"))["report_json"])
    assert stored["citations"][0].get("suggestion") is None

    await merge_citation_fields(db_path, "rid", {"c1": {"suggestion": "NEW"}},
                                owner="fresh")
    stored = json.loads((await get_report(db_path, "rid"))["report_json"])
    assert stored["citations"][0]["suggestion"] == "NEW"


async def test_an_expired_lease_with_no_successor_still_persists(db_path):
    """An analysis that outlived its own lease with nobody taking over has
    still been paid for. Discarding it would leave the citation looking
    unanalysed and it would be bought again — the exact waste the lease
    exists to prevent."""
    await claim_citations(db_path, "rid", ["c1"], "slow", 180)
    await _expire(db_path, "rid", "slow")
    await merge_citation_fields(db_path, "rid", {"c1": {"suggestion": "LATE"}},
                                owner="slow")
    stored = json.loads((await get_report(db_path, "rid"))["report_json"])
    assert stored["citations"][0]["suggestion"] == "LATE"


async def test_releasing_a_claim_allows_an_immediate_retry(db_path):
    """A failed attempt persisted nothing, so the user should not have to wait
    out the lease before trying again."""
    await claim_citations(db_path, "rid", ["c1"], "failed", 180)
    await release_claims(db_path, "rid", "failed")
    assert await claim_citations(db_path, "rid", ["c1"], "retry", 180) == {"c1"}


async def test_finalising_leaves_a_tombstone_and_frees_the_rest(db_path):
    """A written citation keeps its row as a tombstone and can never be claimed
    again; a claimed citation that produced nothing is released so it stays
    retryable."""
    await claim_citations(db_path, "rid", ["c1", "c2"], "first", 180)
    await merge_citation_fields(db_path, "rid", {"c1": {"suggestion": "DONE"}},
                                owner="first")
    assert await claim_citations(db_path, "rid", ["c1", "c2"], "second", 180) == {"c2"}


async def test_endpoint_charges_once_under_concurrent_requests(db_path, monkeypatch):
    """End to end through the route: two simultaneous POSTs, one set of calls."""
    import asyncio
    import app.routes.analyze as analyze

    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(cfg, "LLM_ENABLED", True)
    paid: list[str] = []

    async def fake_suggest(citations, client=None):
        paid.extend(c["id"] for c in citations)
        await asyncio.sleep(0.05)
        return {c["id"]: {"suggestion": f"FIXED {c['id']}",
                          "suggestion_explanation": "", "suggestion_verified": True,
                          "suggestion_rule_basis": [], "suggestion_validation": [],
                          "suggestion_status": None} for c in citations}

    monkeypatch.setattr(analyze, "suggest_fixes", fake_suggest)

    class Resp:
        status_code = 200

    await asyncio.gather(analyze.analyze_report("rid", Resp()),
                         analyze.analyze_report("rid", Resp()))

    assert sorted(paid) == ["c1", "c2"], f"paid for {paid}"
    stored = json.loads((await get_report(db_path, "rid"))["report_json"])
    assert all(c["suggestion"] for c in stored["citations"])


async def test_unique_owners_do_not_collide(db_path):
    """uuid4 hex is what the route uses; two of them must never tie."""
    first = await claim_citations(db_path, "rid", ["c1"], uuid.uuid4().hex, 180)
    second = await claim_citations(db_path, "rid", ["c1"], uuid.uuid4().hex, 180)
    assert first == {"c1"} and second == set()


async def test_a_stale_snapshot_cannot_buy_finished_work_again(db_path, monkeypatch):
    """Cross-review finding. Request A reads the report, then pauses before
    claiming. Request B claims, pays, finishes and releases. A resumes holding
    its old snapshot: the claim succeeds because B's lease is gone, so A paid
    a second time and overwrote B's result. Eligibility is now re-checked after
    the claim, when nobody else can start these citations."""
    import app.routes.analyze as analyze

    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(cfg, "LLM_ENABLED", True)
    paid: list[str] = []

    def payer(tag):
        async def suggest(citations, client=None):
            paid.extend(tag for _ in citations)
            return {c["id"]: {"suggestion": tag, "suggestion_explanation": "",
                              "suggestion_verified": True, "suggestion_rule_basis": [],
                              "suggestion_validation": [], "suggestion_status": None}
                    for c in citations}
        return suggest

    class Resp:
        status_code = 200

    stale = await get_report(db_path, "rid")          # A reads first …

    monkeypatch.setattr(analyze, "suggest_fixes", payer("B"))
    await analyze.analyze_report("rid", Resp())       # … B runs to completion

    real_get = analyze.get_report
    reads = {"n": 0}

    async def a_reads_stale_first(*args, **kwargs):
        reads["n"] += 1
        return stale if reads["n"] == 1 else await real_get(*args, **kwargs)

    monkeypatch.setattr(analyze, "get_report", a_reads_stale_first)
    monkeypatch.setattr(analyze, "suggest_fixes", payer("A"))
    await analyze.analyze_report("rid", Resp())       # A resumes with its snapshot

    assert paid.count("A") == 0, f"A paid again: {paid}"
    stored = json.loads((await get_report(db_path, "rid"))["report_json"])
    assert all(c["suggestion"] == "B" for c in stored["citations"])


async def test_non_positive_lease_is_refused(db_path):
    """A TTL of zero or less would hand out a lease that is already expired,
    so a worker would start paying with no protection at all."""
    with pytest.raises(ValueError):
        await claim_citations(db_path, "rid", ["c1"], "o", 0)


async def test_lease_covers_every_round_of_capped_work():
    """Each citation is cancelled at ANALYSIS_CITATION_DEADLINE_SECONDS and the
    slots run in ceil(n / concurrency) rounds, so the lease must cover that."""
    for n in (1, cfg.LLM_CONCURRENCY, cfg.LLM_CONCURRENCY + 1, 50):
        rounds = -(-n // cfg.LLM_CONCURRENCY)
        assert cfg.analysis_lease_seconds(n) >= \
            rounds * cfg.ANALYSIS_CITATION_DEADLINE_SECONDS, n


async def test_lease_rounds_up_a_fractional_deadline(monkeypatch):
    """Cross-review round 3: int() truncated a fractional deadline, making the
    lease shorter than the work it guards."""
    monkeypatch.setattr(cfg, "ANALYSIS_CITATION_DEADLINE_SECONDS", 10.4)
    monkeypatch.setattr(cfg, "ANALYSIS_LEASE_MARGIN_SECONDS", 0)
    assert cfg.analysis_lease_seconds(1) == 11


async def test_a_call_that_never_ends_is_cancelled_at_the_deadline(monkeypatch):
    """The lease is sized from this deadline, so it has to be real.

    SDK timeouts cannot provide it: httpx applies them per phase, and a 1s
    timeout against a server dripping a byte every 0.5s was measured to return
    successfully after 4s. A call like that would run straight past any lease
    derived from the SDK settings. asyncio.wait_for cancels it instead, and the
    citation stays unanalysed so it can be retried."""
    import asyncio
    import time
    from app.services.fix_suggester import suggest_fixes

    monkeypatch.setattr(cfg, "ANALYSIS_CITATION_DEADLINE_SECONDS", 0.5)

    class NeverAnswers:
        def __init__(self):
            self.messages = self

        async def create(self, **kwargs):
            await asyncio.sleep(3600)

        async def close(self):
            pass

    citation = {"id": "c1", "kind": "reference", "raw_text": "A",
                "issues": [{"type": "format_violation", "reason": "x"}]}
    started = time.monotonic()
    out = await suggest_fixes([citation], client=NeverAnswers())
    assert time.monotonic() - started < 2, "the deadline did not cancel the call"
    assert out == {}, "a cut-off citation must not be recorded"


async def test_tombstones_only_cover_results_that_landed(db_path):
    """Cross-review round 3: tombstones were created from `updates` rather
    than from what was actually written, so an empty or unknown entry left a
    done=1 row with nothing behind it — a citation blocked from retry for as
    long as the report lived."""
    await claim_citations(db_path, "rid", ["c1", "c2"], "o", 180)
    await merge_citation_fields(db_path, "rid",
                                {"c1": {}, "ghost": {"suggestion": "X"}}, owner="o")
    assert await claim_citations(db_path, "rid", ["c1"], "retry", 180) == {"c1"}


def test_paid_clients_are_built_with_the_limits_the_lease_assumes(monkeypatch):
    """Explicit limits keep a call from running for the SDK default of half an
    hour before the deadline cuts it. They no longer bound the lease — the
    deadline does — but a client built without them wastes the slot.

    Records the constructor arguments and stops there. An earlier version let
    the call proceed, which made a real, billed Anthropic request from the unit
    suite on any machine with a key configured."""
    import asyncio
    import anthropic
    import openai
    from app.services import embeddings, fix_suggester

    class _Stop(Exception):
        pass

    seen = {}

    def recorder(name):
        def build(*args, **kwargs):
            seen[name] = kwargs
            raise _Stop          # never reach the network
        return build

    monkeypatch.setattr(anthropic, "AsyncAnthropic", recorder("claude"))
    monkeypatch.setattr(openai, "AsyncOpenAI", recorder("embed"))

    citation = {"id": "c1", "kind": "reference", "raw_text": "A",
                "issues": [{"type": "format_violation", "reason": "x"}]}
    with pytest.raises(_Stop):
        asyncio.run(fix_suggester.suggest_fixes([citation]))
    with pytest.raises(_Stop):
        asyncio.run(embeddings.embed_query("x"))

    assert seen["claude"]["timeout"] == cfg.CLAUDE_TIMEOUT_SECONDS
    assert seen["claude"]["max_retries"] == cfg.LLM_MAX_RETRIES
    assert seen["embed"]["timeout"] == cfg.EMBEDDING_TIMEOUT_SECONDS
    assert seen["embed"]["max_retries"] == cfg.LLM_MAX_RETRIES


async def test_report_removed_mid_analysis_answers_410(db_path, monkeypatch):
    """Answering 200 with the snapshot read at the start told the client the
    request succeeded while showing it nothing."""
    import aiosqlite
    import app.routes.analyze as analyze

    monkeypatch.setattr(cfg, "DB_PATH", db_path)
    monkeypatch.setattr(cfg, "LLM_ENABLED", True)

    async def removes_report(citations, client=None):
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM reports WHERE id='rid'")
            await db.commit()
        return {c["id"]: {"suggestion": "PAID"} for c in citations}

    monkeypatch.setattr(analyze, "suggest_fixes", removes_report)

    class Resp:
        status_code = 200

    resp = Resp()
    await analyze.analyze_report("rid", resp)
    assert resp.status_code == 410


async def test_cleanup_removes_expired_and_orphaned_claims(db_path):
    """Expired rows were otherwise only cleared when the same report was
    claimed again, so a worker killed on a report nobody reopened left its
    rows behind for good."""
    import aiosqlite
    from app.storage.db import delete_stale_claims

    await claim_citations(db_path, "rid", ["c1"], "expired", 180)
    await _expire(db_path, "rid", "expired")
    await claim_citations(db_path, "rid", ["c2"], "orphan", 180)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM reports WHERE id='rid'")
        await db.commit()

    await delete_stale_claims(db_path)

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute("SELECT COUNT(*) FROM citation_claims")
        assert (await cur.fetchone())[0] == 0


async def test_a_stale_worker_cannot_overwrite_a_successor_that_already_finished(db_path):
    """The finalize order cross-review called the most dangerous, and which no
    test covered. A's lease lapses; B takes over, finishes and releases. When
    finalize deleted rows, B's completion left no trace, so A saw no other
    owner and wrote over B's result. B now leaves a tombstone, and A is
    refused."""
    await claim_citations(db_path, "rid", ["c1"], "A", 180)
    await _expire(db_path, "rid", "A")
    assert await claim_citations(db_path, "rid", ["c1"], "B", 180) == {"c1"}

    await merge_citation_fields(db_path, "rid", {"c1": {"suggestion": "B"}}, owner="B")
    await merge_citation_fields(db_path, "rid", {"c1": {"suggestion": "A"}}, owner="A")

    stored = json.loads((await get_report(db_path, "rid"))["report_json"])
    assert stored["citations"][0]["suggestion"] == "B"


async def test_winning_means_the_insert_landed(db_path):
    """The previous winner check filtered on expires_at > now, with now taken
    at the start of the same transaction — every freshly inserted row passed by
    construction. Winners are now the rows this call actually inserted."""
    assert await claim_citations(db_path, "rid", ["c1"], "A", 180) == {"c1"}
    # Same owner again: nothing new lands, so nothing is won.
    assert await claim_citations(db_path, "rid", ["c1"], "A", 180) == set()


async def test_existing_database_gains_the_tombstone_column(tmp_path):
    """CREATE TABLE IF NOT EXISTS never adds a column, so a database created
    before the tombstone existed would otherwise run without it."""
    import aiosqlite
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE citation_claims (report_id TEXT NOT NULL, "
            "citation_id TEXT NOT NULL, owner TEXT NOT NULL, "
            "expires_at TIMESTAMP NOT NULL, PRIMARY KEY (report_id, citation_id))")
        await db.commit()
    await init_db(path)
    await init_db(path)   # idempotent
    assert await claim_citations(path, "r", ["c1"], "o", 60) == {"c1"}


def test_concurrent_workers_can_migrate_the_same_database():
    """Railway boots four workers and each runs init_db. With the column check
    outside a write lock, two could both see `done` missing and the second
    ALTER failed with "duplicate column" — 7 failures in 100 starts across real
    processes. An in-process asyncio version of this test passed 4/4 and would
    have hidden it: the race needs separate processes."""
    import multiprocessing as mp
    import sqlite3
    import tempfile
    from pathlib import Path

    def run_trial() -> list[str]:
        path = str(Path(tempfile.mkdtemp()) / "old.db")
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE citation_claims (report_id TEXT NOT NULL, "
            "citation_id TEXT NOT NULL, owner TEXT NOT NULL, "
            "expires_at TIMESTAMP NOT NULL, PRIMARY KEY (report_id, citation_id))")
        conn.commit()
        conn.close()
        barrier, results = mp.Barrier(4), mp.Queue()
        procs = [mp.Process(target=_migrate_in_child, args=(path, barrier, results))
                 for _ in range(4)]
        for proc in procs:
            proc.start()
        for proc in procs:
            proc.join(timeout=30)
        return [results.get(timeout=5) for _ in procs]

    failures = [r for _ in range(10) for r in run_trial() if r != "ok"]
    assert failures == [], failures
