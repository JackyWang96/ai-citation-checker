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
    assert await claim_citations(db_path, "rid", ["c1"], "crashed", -1) == {"c1"}
    assert await claim_citations(db_path, "rid", ["c1"], "next", 180) == {"c1"}


async def test_a_stale_owner_cannot_overwrite_an_active_owner(db_path):
    """The reason the lease carries an owner at all. A slow worker whose lease
    expired and was taken over must not land its result on top of the worker
    now holding it."""
    await claim_citations(db_path, "rid", ["c1"], "slow", -1)
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
    await claim_citations(db_path, "rid", ["c1"], "slow", -1)
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
