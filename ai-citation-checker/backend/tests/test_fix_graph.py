"""Offline tests for the fix generate/validate/retry loop.

No network: the Anthropic client is faked and retrieval is stubbed, so the
loop's control flow and prompt construction are tested deterministically.
"""
from __future__ import annotations

import json

import pytest

import app.config as cfg
from app.services import fix_graph
from app.services.rules_retriever import RuleChunk

GOOD = ("Zimmerman, B. J., & Cleary, T. J. (2006). Adolescents development. In "
        "F. Pajares & T. Urdan (Eds.), Self-efficacy beliefs of adolescents "
        "(pp. 45-69). Information Age Publishing.")
BAD = ("Zimmerman, B. J. and Cleary, T. J. (2006). Adolescents development. In "
       "F. Pajares & T. Urdan (Eds.), Self-efficacy beliefs of adolescents "
       "(pp. 45-69). Amsterdam: Information Age Publishing.")

CITATION = {
    "id": "c1", "kind": "reference", "raw_text": BAD,
    "issues": [{"type": "format_violation",
                "reason": "R007: Multiple authors joined by ', &' not 'and'.",
                "expected": "', &'", "actual": "'and'"}],
}

CHUNK = RuleChunk(
    chunk_id="apa7-author-separators",
    title="Separating multiple authors",
    text="Use an ampersand before the final author, preceded by a comma.",
    source_url="https://apastyle.apa.org/authors",
    distance=0.4,
)


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _FakeClient:
    """Replays a queued list of payloads and records every prompt sent."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.prompts: list[str] = []
        self.messages = self

    async def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        assert self._payloads, "the loop made more calls than the test queued"
        return _Resp(json.dumps(self._payloads.pop(0)))

    async def close(self):
        pass


@pytest.fixture
def with_rules(monkeypatch):
    async def fake_search(citation, **kw):
        return [CHUNK]
    monkeypatch.setattr(fix_graph.rules_retriever, "search", fake_search)


async def test_valid_suggestion_stops_after_one_call(with_rules):
    client = _FakeClient([{"corrected_reference": GOOD, "explanation": "fixed"}])
    result = await fix_graph.run_fix(CITATION, client)
    assert result["suggestion"] == GOOD
    assert result["suggestion_verified"] is True
    assert result["suggestion_validation"] == []
    assert len(client.prompts) == 1


async def test_retry_prompt_repeats_all_original_material(with_rules):
    """Each attempt is a stateless API call — the model has no memory of the
    previous one, and LangGraph's state never reaches it. A retry that sent
    only the validation failures would strip away the reference and the
    guidance needed to act on them."""
    still_bad = BAD.replace("Amsterdam: ", "")   # fixes R022, leaves R007
    client = _FakeClient([
        {"corrected_reference": still_bad, "explanation": "first try"},
        {"corrected_reference": GOOD, "explanation": "second try"},
    ])
    result = await fix_graph.run_fix(CITATION, client)

    assert len(client.prompts) == 2
    retry = client.prompts[1]
    assert BAD in retry, "retry lost the original reference"
    assert "R007: Multiple authors" in retry, "retry lost the detected problems"
    assert CHUNK.text in retry, "retry lost the retrieved guidance"
    assert still_bad in retry, "retry lost the previous attempt"
    assert "[R007]" in retry, "retry lost the specific validation failure"
    assert result["suggestion_verified"] is True


async def test_unverified_after_max_attempts_is_reported_as_unverified(with_rules):
    """A suggestion that never passes must never be labelled verified — the
    whole point of the loop is that 'verified' means the checker agreed."""
    client = _FakeClient([
        {"corrected_reference": BAD, "explanation": "no change"},
        {"corrected_reference": BAD, "explanation": "still no change"},
    ])
    result = await fix_graph.run_fix(
        {**CITATION, "raw_text": "Something else entirely (2020)."}, client)
    assert result["suggestion_verified"] is False
    assert result["suggestion_validation"], "unverified result must say why"
    assert len(client.prompts) == cfg.LLM_MAX_FIX_ATTEMPTS


async def test_hallucinated_rule_basis_is_dropped(with_rules):
    """rule_basis is model-generated. Showing a chunk that was never retrieved
    would be a fabricated citation used to justify a fix — in a tool built to
    catch fabricated citations."""
    client = _FakeClient([{
        "corrected_reference": GOOD, "explanation": "fixed",
        "rule_basis": ["apa7-author-separators", "apa7-invented-by-the-model"],
    }])
    result = await fix_graph.run_fix(CITATION, client)
    assert result["suggestion_rule_basis"] == ["apa7-author-separators"]


async def test_no_op_rewrite_is_dropped(with_rules):
    """Echoing the reference back unchanged is not a suggestion. The loop
    still retries it first — the validator sees real violations and feeds them
    back — but the unchanged result is dropped rather than shown."""
    echo = {"corrected_reference": BAD, "explanation": "already fine"}
    client = _FakeClient([echo, echo])
    assert await fix_graph.run_fix(CITATION, client) is None
    assert len(client.prompts) == 2


async def test_generation_failure_does_not_retry(with_rules, monkeypatch):
    """A transport failure won't fix itself on the next identical call, and
    spending a second call on it doubles the cost of an outage."""
    calls = 0

    class Boom:
        messages = None

        async def create(self, **kwargs):
            nonlocal calls
            calls += 1
            raise RuntimeError("api down")

    boom = Boom()
    boom.messages = boom
    assert await fix_graph.run_fix(CITATION, boom) is None
    assert calls == 1


async def test_loop_runs_without_retrieval(monkeypatch):
    """Retrieval failing open must leave the loop working — the prompt simply
    carries no guidance section, which is the pre-RAG behaviour."""
    async def empty(citation, **kw):
        return []
    monkeypatch.setattr(fix_graph.rules_retriever, "search", empty)
    client = _FakeClient([{"corrected_reference": GOOD, "explanation": "fixed"}])
    result = await fix_graph.run_fix(CITATION, client)
    assert result["suggestion_verified"] is True
    assert "Relevant APA 7th guidance" not in client.prompts[0]
