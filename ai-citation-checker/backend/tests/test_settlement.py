"""Per-citation settlement: what each analysis attempt cost, and what that
leaves the citation allowed to spend.

The invariant under test is spend, not any single failure: within one report,
a citation is charged at most ANALYSIS_MAX_BILLED_ATTEMPTS times. Before this,
a response that was charged for and then failed to parse was released for
retry — and retried, and charged, on every click. Measured on main: five
clicks, five charges, status never settled.

Grouped by where an edge case lives:
  A. classification — did this attempt cost money?
  B. the budget — counting, refunding, capping
  C. the sweep — a budget spent by a worker that died
  D. end to end, through the route
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import aiosqlite
import anthropic
import httpx
import pytest

import app.config as cfg
from app.services import fix_graph
from app.services.fix_suggester import _fixable
from app.storage.db import (
    claim_citations,
    delete_stale_claims,
    fail_exhausted_citations,
    get_report,
    init_db,
    merge_citation_fields,
    release_claims,
    save_report,
)

RAW = "Smith, J. and Jones, A. (2020). A study. Journal of Testing, 5(2), 1-10."
FIXED = "Smith, J., & Jones, A. (2020). A study. Journal of Testing, 5(2), 1-10."
CITATION = {"id": "c1", "kind": "reference", "raw_text": RAW,
            "issues": [{"type": "format_violation", "reason": "R007"}]}
_REQ = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


# ── fakes ────────────────────────────────────────────────────────────────────

def _text(text: str):
    class Block:
        type = "text"

    block = Block()
    block.text = text

    class Resp:
        content = [block]
    return Resp()


def _no_text_block():
    """What a refusal or tool-only response looks like to this code."""
    class Resp:
        content = []
    return Resp()


def _status(cls, code: int):
    return cls("err", response=httpx.Response(code, request=_REQ), body=None)


class Scripted:
    """Plays back one item per call: a response object, or an exception."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self):
        pass


@pytest.fixture
def no_rag(monkeypatch):
    monkeypatch.setattr(cfg, "RAG_ENABLED", False)


@pytest.fixture
async def db_path(monkeypatch):
    path = str(Path(tempfile.mkdtemp()) / "reports.db")
    await init_db(path)
    await save_report(path, "rid", json.dumps({"citations": [dict(CITATION)]}), "f.docx")
    monkeypatch.setattr(cfg, "DB_PATH", path)
    monkeypatch.setattr(cfg, "LLM_ENABLED", True)
    monkeypatch.setattr(cfg, "RAG_ENABLED", False)
    return path


async def _attempts(db_path: str, citation_id: str = "c1"):
    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(
            "SELECT attempts, done FROM citation_claims WHERE citation_id=?",
            (citation_id,))
        return await cur.fetchone()


async def _stored(db_path: str, citation_id: str = "c1") -> dict:
    report = json.loads((await get_report(db_path, "rid"))["report_json"])
    return next(c for c in report["citations"] if c["id"] == citation_id)


async def _expire_all(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE citation_claims SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE done=0")
        await db.commit()


# ── A. classification ────────────────────────────────────────────────────────

async def test_usable_response_is_billed_and_kept(no_rag):
    out = await fix_graph.run_fix_outcome(
        CITATION, Scripted(_text(json.dumps({"corrected_reference": FIXED,
                                             "explanation": "x"}))))
    assert out.payload["suggestion"] == FIXED
    assert out.billed


@pytest.mark.parametrize("response", [
    _text("not json at all"),
    _text('{"corrected_reference": "Smith, J. (20'),          # truncated at max_tokens
    _text(json.dumps({"corrected_reference": "", "explanation": "x"})),
    _text(json.dumps({"explanation": "no correction field"})),
    _no_text_block(),
], ids=["unparseable", "truncated", "empty-correction", "missing-field", "no-text-block"])
async def test_a_response_that_cannot_be_used_was_still_charged(no_rag, response):
    """The bug this file exists for. Each of these is a response the API
    returned and billed; one `except` used to file them with requests that
    never reached the API, and all of them were released for a free-looking
    retry."""
    out = await fix_graph.run_fix_outcome(CITATION, Scripted(response))
    assert out.payload is None
    assert out.billed


@pytest.mark.parametrize("error", [
    anthropic.APIConnectionError(request=_REQ),
    _status(anthropic.InternalServerError, 500),
    _status(anthropic.RateLimitError, 429),
    _status(anthropic.BadRequestError, 400),
    _status(anthropic.AuthenticationError, 401),
], ids=["connection", "500", "429", "400", "401"])
async def test_an_error_before_any_response_charged_nothing(no_rag, error):
    """The API answered with an error or was never reached: no completion,
    no charge. 400 and 401 are refunded too — they are usually a deploy or
    key problem affecting every citation, and spending each one's budget on
    it would mark a whole report failed for a fault that is not theirs."""
    out = await fix_graph.run_fix_outcome(CITATION, Scripted(error))
    assert out.payload is None
    assert not out.billed


async def test_an_sdk_timeout_counts_as_charged(no_rag):
    """APITimeoutError subclasses APIConnectionError, so checking the parent
    first would refund it. A timeout means the client stopped waiting, not
    that the server stopped working; it may have finished and billed."""
    assert issubclass(anthropic.APITimeoutError, anthropic.APIConnectionError)
    out = await fix_graph.run_fix_outcome(
        CITATION, Scripted(anthropic.APITimeoutError(request=_REQ)))
    assert out.payload is None
    assert out.billed


async def test_an_unrecognised_failure_counts_as_charged(no_rag):
    """Never refund on a guess: an unbounded refund is an unbounded budget."""
    out = await fix_graph.run_fix_outcome(CITATION, Scripted(RuntimeError("?")))
    assert out.billed


async def test_a_charge_survives_a_later_free_failure(no_rag):
    """LangGraph overwrites keys a node returns. If a failed retry returned
    billed=False it would erase the first attempt's charge, and the whole run
    would be refunded despite having been paid for."""
    draft = _text(json.dumps({"corrected_reference": RAW.replace("A study", "A Study"),
                              "explanation": "x"}))       # charged, still fails R007
    out = await fix_graph.run_fix_outcome(
        CITATION, Scripted(draft, anthropic.APIConnectionError(request=_REQ)))
    assert out.billed


async def test_a_failed_retry_keeps_the_paid_draft(no_rag):
    """Found while writing this file. A failed retry used to blank the
    suggestion, throwing away the first attempt's draft — paid for, merely
    unverified — and leaving the citation to be bought again."""
    draft_text = RAW.replace("A study", "A Study")
    draft = _text(json.dumps({"corrected_reference": draft_text, "explanation": "x"}))
    out = await fix_graph.run_fix_outcome(
        CITATION, Scripted(draft, anthropic.APIConnectionError(request=_REQ)))
    assert out.payload["suggestion"] == draft_text
    assert out.payload["suggestion_verified"] is False


async def test_a_generation_failure_still_ends_the_loop(no_rag, monkeypatch):
    """Keeping the draft must not turn an outage into extra calls. The stop
    signal moved from "suggestion is empty" to its own flag; with a higher
    attempt limit, a failure must still end the run."""
    monkeypatch.setattr(cfg, "LLM_MAX_FIX_ATTEMPTS", 5)
    draft = _text(json.dumps({"corrected_reference": RAW.replace("A study", "A Study"),
                              "explanation": "x"}))
    client = Scripted(draft, anthropic.APIConnectionError(request=_REQ))
    await fix_graph.run_fix_outcome(CITATION, client)
    assert client.calls == 2


# ── B. the budget ────────────────────────────────────────────────────────────

async def test_a_claim_spends_an_attempt_before_any_money_moves(db_path):
    """Counted up front, so a worker that dies after paying has still used
    its attempt."""
    assert await claim_citations(db_path, "rid", ["c1"], "a", 60) == {"c1"}
    assert await _attempts(db_path) == (1, 0)


async def test_a_refund_returns_the_attempt_and_never_goes_below_zero(db_path):
    await claim_citations(db_path, "rid", ["c1"], "a", 60)
    await release_claims(db_path, "rid", "a", refund={"c1"})
    await release_claims(db_path, "rid", "a", refund={"c1"})
    assert (await _attempts(db_path))[0] == 0


async def test_the_budget_caps_claims(db_path):
    for owner in ("a", "b"):
        assert await claim_citations(db_path, "rid", ["c1"], owner, 60) == {"c1"}
        await release_claims(db_path, "rid", owner)
    assert await claim_citations(db_path, "rid", ["c1"], "c", 60) == set()


async def test_free_failures_never_spend_the_budget(db_path):
    """An outage that lasts many clicks must leave the citation retryable."""
    for n in range(10):
        owner = f"o{n}"
        assert await claim_citations(db_path, "rid", ["c1"], owner, 60) == {"c1"}
        await release_claims(db_path, "rid", owner, refund={"c1"})
    assert (await _attempts(db_path))[0] == 0


async def test_releasing_keeps_the_count_and_cleanup_spares_it(db_path):
    """Releasing used to delete the row. That reset the count, and with it
    the cap: every released attempt came back as a fresh budget."""
    await claim_citations(db_path, "rid", ["c1"], "a", 60)
    await release_claims(db_path, "rid", "a")
    await delete_stale_claims(db_path)
    assert (await _attempts(db_path))[0] == 1


async def test_cleanup_removes_rows_only_once_their_report_is_gone(db_path):
    await claim_citations(db_path, "rid", ["c1"], "a", 60)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM reports")
        await db.commit()
    await delete_stale_claims(db_path)
    assert await _attempts(db_path) is None


async def test_spending_the_last_attempt_marks_the_citation_failed(db_path):
    """Settled on the report, not only in the lease table — otherwise the page
    keeps offering a button that can no longer win a claim."""
    await claim_citations(db_path, "rid", ["c1"], "a", 60)
    await merge_citation_fields(db_path, "rid", {}, owner="a")       # charged, nothing usable
    assert (await _stored(db_path)).get("suggestion_status") is None  # one left

    await claim_citations(db_path, "rid", ["c1"], "b", 60)
    await merge_citation_fields(db_path, "rid", {}, owner="b")
    stored = await _stored(db_path)
    assert stored["suggestion_status"] == "failed"
    assert await _attempts(db_path) == (2, 1)      # tombstoned
    assert not _fixable(stored)


async def test_one_batch_settles_each_citation_its_own_way(db_path):
    """Fixed, charged-and-failed, and free-failure in a single settlement."""
    report = {"citations": [dict(CITATION, id=i) for i in ("fixed", "paid", "free")]}
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE reports SET report_json=?", (json.dumps(report),))
        await db.commit()
    await claim_citations(db_path, "rid", ["fixed", "paid", "free"], "a", 60)
    await merge_citation_fields(db_path, "rid", {"fixed": {"suggestion": FIXED}},
                                owner="a", refund={"free"})
    assert await _attempts(db_path, "fixed") == (1, 1)    # tombstone
    assert await _attempts(db_path, "paid") == (1, 0)     # counted, retryable
    assert await _attempts(db_path, "free") == (0, 0)     # refunded


async def test_losing_a_claim_costs_nothing(db_path):
    await claim_citations(db_path, "rid", ["c1"], "winner", 60)
    assert await claim_citations(db_path, "rid", ["c1"], "loser", 60) == set()
    assert (await _attempts(db_path))[0] == 1


async def test_a_refund_cannot_reach_a_finished_citation(db_path):
    """A refund aimed at a citation that was also written is ignored: written
    means paid, and the tombstone must not lose its count."""
    await claim_citations(db_path, "rid", ["c1"], "a", 60)
    await merge_citation_fields(db_path, "rid", {"c1": {"suggestion": FIXED}},
                                owner="a", refund={"c1"})
    assert await _attempts(db_path) == (1, 1)


async def test_existing_databases_gain_the_attempts_column(tmp_path):
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE citation_claims (report_id TEXT NOT NULL, "
            "citation_id TEXT NOT NULL, owner TEXT NOT NULL, "
            "expires_at TIMESTAMP NOT NULL, done INTEGER NOT NULL DEFAULT 0, "
            "PRIMARY KEY (report_id, citation_id))")
        await db.execute("INSERT INTO citation_claims VALUES ('r','c1','o','2000-01-01',0)")
        await db.commit()
    await init_db(path)
    await init_db(path)
    async with aiosqlite.connect(path) as db:
        row = await (await db.execute("SELECT attempts FROM citation_claims")).fetchone()
    assert row[0] == 1, "rows from before the column existed were each claimed once"


# ── C. the sweep ─────────────────────────────────────────────────────────────

async def test_a_worker_that_died_on_the_last_attempt_is_settled_by_the_next_request(db_path):
    """Nobody marked the citation failed: its worker died holding the last
    attempt. The lease lapses with the budget spent, no claim can win it, and
    without a sweep the report shows it as analysable forever."""
    await claim_citations(db_path, "rid", ["c1"], "a", 60)
    await release_claims(db_path, "rid", "a")
    await claim_citations(db_path, "rid", ["c1"], "dies", 60)
    await _expire_all(db_path)
    report = await fail_exhausted_citations(db_path, "rid", ["c1"])
    assert report is not None
    assert (await _stored(db_path))["suggestion_status"] == "failed"


async def test_the_sweep_leaves_a_live_last_attempt_alone(db_path):
    """Someone is still working on it; their result may yet land."""
    await claim_citations(db_path, "rid", ["c1"], "a", 60)
    await release_claims(db_path, "rid", "a")
    await claim_citations(db_path, "rid", ["c1"], "still-working", 60)
    assert await fail_exhausted_citations(db_path, "rid", ["c1"]) is None
    assert (await _stored(db_path)).get("suggestion_status") is None


async def test_the_sweep_never_downgrades_a_finished_citation(db_path):
    await claim_citations(db_path, "rid", ["c1"], "a", 60)
    await release_claims(db_path, "rid", "a")
    await claim_citations(db_path, "rid", ["c1"], "b", 60)
    async with aiosqlite.connect(db_path) as db:   # finished outside the lease
        report = {"citations": [dict(CITATION, suggestion=FIXED)]}
        await db.execute("UPDATE reports SET report_json=?", (json.dumps(report),))
        await db.commit()
    await _expire_all(db_path)
    await fail_exhausted_citations(db_path, "rid", ["c1"])
    stored = await _stored(db_path)
    assert stored["suggestion"] == FIXED
    assert stored.get("suggestion_status") is None


# ── D. end to end ────────────────────────────────────────────────────────────

class _Resp:
    status_code = 200


def _client_factory(monkeypatch, *script):
    client = Scripted(*script)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda *a, **k: client)
    return client


async def test_charged_failures_stop_at_the_budget(db_path, monkeypatch):
    """The reproduction. On main five clicks cost five calls and the citation
    never settled; now the budget is spent, the citation is failed, and
    further clicks cost nothing."""
    import app.routes.analyze as analyze
    client = _client_factory(monkeypatch, _text('{"corrected_reference": "Smith'))
    for _ in range(5):
        await analyze.analyze_report("rid", _Resp())
    assert client.calls == cfg.ANALYSIS_MAX_BILLED_ATTEMPTS
    assert (await _stored(db_path))["suggestion_status"] == "failed"


async def test_an_outage_never_spends_the_budget(db_path, monkeypatch):
    import app.routes.analyze as analyze
    client = _client_factory(monkeypatch, _status(anthropic.InternalServerError, 500))
    for _ in range(5):
        await analyze.analyze_report("rid", _Resp())
    assert client.calls == 5, "each click should still try"
    assert (await _attempts(db_path))[0] == 0
    assert _fixable(await _stored(db_path)), "must stay on offer once the outage ends"


async def test_hitting_the_deadline_counts_as_charged(db_path, monkeypatch):
    """Cancelled mid-call: the response may have been produced and billed."""
    import app.routes.analyze as analyze
    monkeypatch.setattr(cfg, "ANALYSIS_CITATION_DEADLINE_SECONDS", 0.2)

    class Slow(Scripted):
        async def create(self, **kwargs):
            self.calls += 1
            await asyncio.sleep(5)

    client = Slow()
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda *a, **k: client)
    await analyze.analyze_report("rid", _Resp())
    assert (await _attempts(db_path))[0] == 1


async def test_a_citation_found_finished_after_claiming_is_refunded(db_path, monkeypatch):
    """Claimed from a stale snapshot, then found already finished on re-read.
    Nothing was paid, so the claim's attempt goes back."""
    import app.routes.analyze as analyze
    stale = await get_report(db_path, "rid")
    async with aiosqlite.connect(db_path) as db:
        report = {"citations": [dict(CITATION, suggestion=FIXED)]}
        await db.execute("UPDATE reports SET report_json=?", (json.dumps(report),))
        await db.commit()

    real_get, reads = analyze.get_report, {"n": 0}

    async def stale_first(*args, **kwargs):
        reads["n"] += 1
        return stale if reads["n"] == 1 else await real_get(*args, **kwargs)

    monkeypatch.setattr(analyze, "get_report", stale_first)
    client = _client_factory(monkeypatch, _text("unused"))
    await analyze.analyze_report("rid", _Resp())
    assert client.calls == 0
    assert (await _attempts(db_path))[0] == 0


async def test_a_report_gone_after_claiming_refunds_everything(db_path, monkeypatch):
    import app.routes.analyze as analyze
    real_get, reads = analyze.get_report, {"n": 0}

    async def vanishes_on_reread(*args, **kwargs):
        reads["n"] += 1
        return await real_get(*args, **kwargs) if reads["n"] == 1 else None

    monkeypatch.setattr(analyze, "get_report", vanishes_on_reread)
    resp = _Resp()
    await analyze.analyze_report("rid", resp)
    assert resp.status_code == 410
    assert (await _attempts(db_path))[0] == 0


@pytest.mark.parametrize("status", ["declined", "failed"])
def test_terminal_citations_are_never_offered_again(status):
    assert not _fixable(dict(CITATION, suggestion_status=status))
