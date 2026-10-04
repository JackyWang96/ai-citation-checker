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
    """The core guarantee: whoever loses the claim must not analyse, so the
    same rewrite is never bought twice."""
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


async def test_finalising_frees_the_lease(db_path):
    await claim_citations(db_path, "rid", ["c1"], "first", 180)
    await merge_citation_fields(db_path, "rid", {"c1": {"suggestion": "DONE"}},
                                owner="first")
    assert await claim_citations(db_path, "rid", ["c1"], "second", 180) == {"c1"}


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


async def test_lease_scales_with_the_work():
    """A fixed lease was a guess. Large reports queue behind the concurrency
    limit, and once their lease ran out a second tab could take over and pay
    again."""
    small = cfg.analysis_lease_seconds(1)
    large = cfg.analysis_lease_seconds(50)
    assert large > small
    rounds = -(-50 // cfg.LLM_CONCURRENCY)
    assert large >= rounds * cfg.ANALYSIS_LEASE_PER_ROUND_SECONDS


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
