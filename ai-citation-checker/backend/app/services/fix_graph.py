"""The generate → validate → retry loop behind AI fix suggestions.

Three nodes: retrieve the APA rules that bear on the detected problem, ask
Claude to rewrite the reference with those rules in front of it, then check
the rewrite against the same deterministic rules the checker applies to the
user's document. A failed check feeds the specific failures back and asks
again.

Two things make this different from a single call:

* the model sees the actual rule text instead of relying on recall, and
* nothing reaches the user labelled "verified" unless it passed the checker.

Retrieval and validation are free relative to generation, so the loop costs
at most one extra Claude call per citation.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, NamedTuple, Optional, TypedDict

import anthropic
from langgraph.graph import END, StateGraph

import app.config as cfg
from app.services import rules_retriever
from app.services.rules_retriever import _FIXABLE_ISSUE_TYPES
from app.services.suggestion_validator import validate_suggestion

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an APA 7th edition reference-formatting expert. You are given one "
    "reference exactly as a student wrote it, the specific problems an "
    "automated checker detected, and the APA 7th guidance that applies. "
    "Rewrite the reference so it is correct APA 7th edition, fixing only the "
    "detected problems and any clear formatting errors. Follow the supplied "
    "guidance over your own recollection. Do not invent authors, titles, "
    "years, DOIs, page numbers, or any bibliographic fact that is not already "
    "present in the reference or the checker's expected values — if a required "
    "element is genuinely missing, leave a clearly marked placeholder like "
    "[page range] rather than fabricating it. Preserve the original work being "
    "cited. Return the corrected reference, a one-sentence explanation of what "
    "you changed, and the ids of the guidance entries you relied on."
)

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "corrected_reference": {"type": "string"},
        "explanation": {"type": "string"},
        "rule_basis": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["corrected_reference", "explanation"],
    "additionalProperties": False,
}


class FixState(TypedDict, total=False):
    citation: dict
    client: Any            # AsyncAnthropic; never serialised (no checkpointer)
    rules: list[Any]       # RuleChunk
    retrieval_ok: bool
    billed: bool           # sticky: once any Claude call returned, never reset
    generation_failed: bool
    suggestion: str
    explanation: str
    rule_basis: list[dict]
    validation: list[str]
    attempts: int
    verified: bool


def _normalise(text: str) -> str:
    """Collapse whitespace for a no-op comparison.

    Case is deliberately preserved: recapitalising a journal name (R018) is a
    real fix, so lowercasing here would discard valid suggestions.
    """
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _problems(citation: dict) -> str:
    lines = []
    for i in citation.get("issues") or []:
        if i.get("type") not in _FIXABLE_ISSUE_TYPES:
            continue
        parts = [f"- {i.get('reason', 'issue')}"]
        if i.get("expected"):
            parts.append(f"(expected: {i['expected']})")
        if i.get("actual"):
            parts.append(f"(found: {i['actual']})")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def build_user_prompt(state: FixState) -> str:
    """Every attempt carries the full original material.

    Each retry is a separate stateless API call — LangGraph's state lives in
    this process and never reaches the model. Sending only the validation
    failures would strip the reference and the guidance the model needs to act
    on them, so the whole prompt is rebuilt and the failures are appended.
    """
    citation = state["citation"]
    blocks = [f"Reference:\n{citation['raw_text']}\n",
              f"Detected problems:\n{_problems(citation)}"]

    if state.get("rules"):
        guidance = "\n\n".join(
            f"[{c.chunk_id}] {c.title}\n{c.text}\nSource: {c.source_url}"
            for c in state["rules"]
        )
        blocks.append(f"\nRelevant APA 7th guidance:\n{guidance}")

    if state.get("validation"):
        failures = "\n".join(f"- {v}" for v in state["validation"])
        blocks.append(
            "\n--- Your previous attempt did not pass the automated check ---\n"
            f"\nYour previous attempt:\n{state.get('suggestion', '')}\n"
            f"\nValidation failures:\n{failures}\n"
            "\nFix these specific failures while keeping everything else "
            "correct. Do not introduce new bibliographic facts."
        )
    return "\n".join(blocks)


async def retrieve_rules(state: FixState) -> FixState:
    """Never raises: `search` already degrades to [] on any failure, and an
    empty rules list simply drops the guidance section from the prompt,
    leaving the model at its pre-RAG quality."""
    result = await rules_retriever.search(state["citation"])
    return {"rules": result.chunks, "retrieval_ok": result.ok}


async def generate_fix(state: FixState) -> FixState:
    """One Claude attempt.

    The call and the parse are separate failure points because they are
    billed differently. One `except` used to cover both, so a response that
    was charged for and then failed to parse looked exactly like a request
    that never reached the API — and both were released for retry, paying
    again for the parse failure on every click.

    `billed` is only ever set True, never False: LangGraph overwrites keys a
    node returns, so returning False on a later failed attempt would erase an
    earlier attempt's charge.

    A failed attempt leaves `suggestion` alone rather than blanking it. When a
    retry fails, the previous attempt's draft — unverified, but paid for — is
    still the best answer available. Blanking it threw that draft away and
    left the citation looking unanalysed, to be paid for again.
    `generation_failed` carries the "stop retrying" signal instead.
    """
    attempts = state.get("attempts", 0) + 1
    try:
        resp = await state["client"].messages.create(
            model=cfg.LLM_MODEL,
            max_tokens=1024,
            system=[{"type": "text", "text": _SYSTEM_PROMPT,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": build_user_prompt(state)}],
            output_config={"format": {"type": "json_schema",
                                      "schema": _OUTPUT_SCHEMA}},
        )
    except anthropic.APITimeoutError:
        # Checked before APIConnectionError, which it subclasses. The client
        # stopped waiting; the server may well have finished and charged.
        logger.warning("fix generation timed out; treating as billed", exc_info=True)
        return {"attempts": attempts, "generation_failed": True, "billed": True}
    except (anthropic.APIConnectionError, anthropic.APIStatusError):
        # The API answered with an error, or was never reached. No completion
        # was produced, so nothing was charged.
        logger.warning("fix generation failed before a response", exc_info=True)
        return {"attempts": attempts, "generation_failed": True}
    except Exception:
        # Unknown: never refund on a guess.
        logger.warning("fix generation failed unexpectedly", exc_info=True)
        return {"attempts": attempts, "generation_failed": True, "billed": True}

    # A response exists from here on, so it was charged whether or not it is
    # usable. Truncated output (max_tokens) and refusals both land here.
    try:
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
    except Exception:
        logger.warning("fix response could not be parsed (billed)", exc_info=True)
        return {"attempts": attempts, "generation_failed": True, "billed": True}

    # rule_basis comes from the model and may name chunks that were never
    # retrieved. Showing a hallucinated rule as the justification for a fix
    # would be its own fabricated citation, in a tool built to catch those.
    # Resolve against the chunks we actually retrieved, and take the title and
    # URL from those rows. The model supplies ids only; anything it invents is
    # dropped, and it never gets to name a source link.
    retrieved = {c.chunk_id: c for c in state.get("rules") or []}
    # Deduplicated, first occurrence wins. Filtering on membership alone let
    # the model repeat one valid id as often as it liked — 100 ids produced 50
    # entries — which floods the card and gives React duplicate keys. The
    # retrieved set is the natural cap: nothing else can legitimately appear.
    seen: set[str] = set()
    basis = []
    for cid in data.get("rule_basis") or []:
        c = retrieved.get(cid)
        if c is None or cid in seen:
            continue
        seen.add(cid)
        basis.append(
            {"chunk_id": c.chunk_id, "title": c.title, "source_url": c.source_url}
        )

    return {
        "attempts": attempts,
        "suggestion": (data.get("corrected_reference") or "").strip(),
        "explanation": (data.get("explanation") or "").strip(),
        "rule_basis": basis,
        "billed": True,
        "generation_failed": False,
    }


async def validate_fix(state: FixState) -> FixState:
    suggestion = state.get("suggestion") or ""
    if not suggestion:
        return {"validation": ["generation failed"], "verified": False}
    failures = validate_suggestion(state["citation"], suggestion)
    return {"validation": failures, "verified": not failures}


def should_retry(state: FixState) -> str:
    if state.get("generation_failed"):
        # Retrying a transport failure spends another call on the same outage.
        # Checked by flag, not by an empty suggestion: a failed retry now keeps
        # the previous attempt's draft in place.
        return "done"
    if not state.get("validation"):
        return "done"
    if not state.get("suggestion"):
        return "done"
    if state.get("attempts", 0) >= cfg.LLM_MAX_FIX_ATTEMPTS:
        return "done"
    return "retry"


def _build_graph():
    g = StateGraph(FixState)
    g.add_node("retrieve", retrieve_rules)
    g.add_node("generate", generate_fix)
    g.add_node("validate", validate_fix)
    g.set_entry_point("retrieve")
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "validate")
    # Retry returns to generate, not retrieve: the rules cannot change between
    # attempts, so re-retrieving would spend an embedding call on the same
    # query and get the same chunks.
    g.add_conditional_edges("validate", should_retry,
                            {"retry": "generate", "done": END})
    return g.compile()


_graph = _build_graph()


class FixOutcome(NamedTuple):
    """What one citation produced, and whether producing it cost money.

    `payload` None means nothing usable came back and the citation stays
    retryable. `billed` decides what that retry costs the user's budget: a
    failure that charged nothing is refunded, one that charged — or might
    have — counts toward the per-citation cap.
    """
    payload: Optional[dict]
    billed: bool


async def run_fix_outcome(
    citation: dict, client: anthropic.AsyncAnthropic
) -> FixOutcome:
    """Run the loop for one citation and settle what it produced.

    Three ways it ends. A usable rewrite, verified or not. A considered
    decline — the model saw the rules and judged the reference correct —
    recorded as status "declined" so it is not re-analysed. Or nothing usable,
    which leaves the citation retryable; `billed` then says whether that try
    cost money."""
    final = await _graph.ainvoke({"citation": citation, "client": client,
                                  "attempts": 0, "billed": False})
    billed = bool(final.get("billed"))
    suggestion = final.get("suggestion") or ""
    if not suggestion:
        # Nothing usable. Retryable either way; `billed` separates a request
        # that never reached the API from a response that was charged for and
        # then failed to parse or came back truncated.
        return FixOutcome(None, billed)

    # The model sometimes concludes no change is needed but still echoes the
    # reference back. Rendering that as a suggestion shows the user an "AI fix"
    # identical to what they wrote.
    if _normalise(suggestion) == _normalise(citation.get("raw_text", "")):
        if not final.get("retrieval_ok", True):
            # Retrieval was down, so the model judged this with no rules in
            # front of it. Recording that as a considered decline would make an
            # outage permanent. It stays retryable — and the call was charged,
            # so the retry counts.
            return FixOutcome(None, billed)
        # Recorded: the model gave a considered answer, and asking again costs
        # another embedding and Claude call to get the same one.
        return FixOutcome({
            "suggestion": None,
            "suggestion_explanation": "",
            "suggestion_verified": False,
            "suggestion_rule_basis": [],
            "suggestion_validation": [],
            "suggestion_status": "declined",
        }, billed)
    return FixOutcome({
        "suggestion": suggestion,
        "suggestion_explanation": final.get("explanation", ""),
        "suggestion_verified": bool(final.get("verified")),
        "suggestion_rule_basis": final.get("rule_basis") or [],
        "suggestion_validation": final.get("validation") or [],
        "suggestion_status": None,
    }, billed)


async def run_fix(
    citation: dict, client: anthropic.AsyncAnthropic
) -> Optional[dict]:
    """The payload only, for callers that do not settle billing."""
    return (await run_fix_outcome(citation, client)).payload
