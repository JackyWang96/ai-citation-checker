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
from typing import Any, Optional, TypedDict

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
    suggestion: str
    explanation: str
    rule_basis: list[str]
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
    return {"rules": await rules_retriever.search(state["citation"])}


async def generate_fix(state: FixState) -> FixState:
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
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
    except Exception:
        logger.warning("fix generation failed", exc_info=True)
        return {"attempts": attempts, "suggestion": ""}

    # rule_basis comes from the model and may name chunks that were never
    # retrieved. Showing a hallucinated rule as the justification for a fix
    # would be its own fabricated citation, in a tool built to catch those.
    retrieved = {c.chunk_id for c in state.get("rules") or []}
    basis = [c for c in (data.get("rule_basis") or []) if c in retrieved]

    return {
        "attempts": attempts,
        "suggestion": (data.get("corrected_reference") or "").strip(),
        "explanation": (data.get("explanation") or "").strip(),
        "rule_basis": basis,
    }


async def validate_fix(state: FixState) -> FixState:
    suggestion = state.get("suggestion") or ""
    if not suggestion:
        return {"validation": ["generation failed"], "verified": False}
    failures = validate_suggestion(suggestion)
    return {"validation": failures, "verified": not failures}


def should_retry(state: FixState) -> str:
    if not state.get("validation"):
        return "done"
    if not state.get("suggestion"):
        return "done"          # generation failed; retrying won't help
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


async def run_fix(
    citation: dict, client: anthropic.AsyncAnthropic
) -> Optional[dict]:
    """Return the suggestion for one citation, or None if nothing usable came
    back. A suggestion that never passed validation is still returned, marked
    unverified — the caller decides how to present it."""
    final = await _graph.ainvoke({"citation": citation, "client": client,
                                  "attempts": 0})
    suggestion = final.get("suggestion") or ""
    if not suggestion:
        return None
    # The model sometimes concludes no change is needed but still echoes the
    # reference back. Rendering that as a suggestion shows the user an "AI fix"
    # identical to what they wrote.
    if _normalise(suggestion) == _normalise(citation.get("raw_text", "")):
        return None
    return {
        "suggestion": suggestion,
        "suggestion_explanation": final.get("explanation", ""),
        "suggestion_verified": bool(final.get("verified")),
        "suggestion_rule_basis": final.get("rule_basis") or [],
        "suggestion_validation": final.get("validation") or [],
    }
