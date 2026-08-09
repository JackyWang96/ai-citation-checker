"""Stage 2 — LLM-generated APA 7th fix suggestions (opt-in, on demand).

Given citations the deterministic pipeline already flagged with format or
field issues, ask Claude to produce a corrected APA 7th reference string.
This never runs during a normal check — it is triggered by the user from the
report page, only when an API key is configured.
"""
from __future__ import annotations
import asyncio
import json
from typing import Optional
import anthropic
import app.config as cfg

# Issue types that a rewrite can plausibly fix. A "not_found" reference can't
# be corrected by reformatting, so those are left alone.
_FIXABLE_ISSUE_TYPES = frozenset({"format_violation", "field_mismatch"})

# Stable across every request → cached (prompt caching keys on the prefix).
_SYSTEM_PROMPT = (
    "You are an APA 7th edition reference-formatting expert. You are given one "
    "reference exactly as a student wrote it, plus the specific problems an "
    "automated checker detected. Rewrite the reference so it is correct APA "
    "7th edition, fixing only the detected problems and any clear formatting "
    "errors. Do not invent authors, titles, years, DOIs, page numbers, or any "
    "bibliographic fact that is not already present in the reference or the "
    "checker's expected values — if a required element is genuinely missing, "
    "leave a clearly marked placeholder like [page range] rather than "
    "fabricating it. Preserve the original work being cited. Return the single "
    "corrected reference string and a one-sentence explanation of what you "
    "changed."
)

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "corrected_reference": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["corrected_reference", "explanation"],
    "additionalProperties": False,
}


def _fixable(citation: dict) -> bool:
    """A reference citation carrying at least one rewrite-fixable issue."""
    if citation.get("kind") != "reference":
        return False
    if citation.get("suggestion"):   # already analysed — don't pay twice
        return False
    return any(
        i.get("type") in _FIXABLE_ISSUE_TYPES
        for i in citation.get("issues") or []
    )


def _build_user_prompt(citation: dict) -> str:
    lines = [f"Reference:\n{citation['raw_text']}\n", "Detected problems:"]
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


async def _suggest_one(client: anthropic.AsyncAnthropic, citation: dict,
                       sem: asyncio.Semaphore) -> Optional[dict]:
    """Return {'suggestion', 'suggestion_explanation'} or None on any failure.

    A single citation's failure must never fail the whole batch — the report
    is still valid without a suggestion.
    """
    async with sem:
        try:
            resp = await client.messages.create(
                model=cfg.LLM_MODEL,
                max_tokens=1024,
                system=[{
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": _build_user_prompt(citation)}],
                output_config={"format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA}},
            )
        except Exception:
            return None

    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    corrected = (data.get("corrected_reference") or "").strip()
    if not corrected:
        return None
    return {
        "suggestion": corrected,
        "suggestion_explanation": (data.get("explanation") or "").strip(),
    }


async def suggest_fixes(
    citations: list[dict],
    client: Optional[anthropic.AsyncAnthropic] = None,
) -> dict[str, dict]:
    """Map citation id -> {'suggestion', 'suggestion_explanation'} for every
    fixable citation that produced a usable rewrite. `client` is injectable for
    tests; in production it defaults to a key-configured AsyncAnthropic."""
    targets = [c for c in citations if _fixable(c)]
    if not targets:
        return {}

    owns_client = client is None
    if owns_client:
        client = anthropic.AsyncAnthropic(api_key=cfg.ANTHROPIC_API_KEY)

    sem = asyncio.Semaphore(cfg.LLM_CONCURRENCY)
    try:
        results = await asyncio.gather(
            *[_suggest_one(client, c, sem) for c in targets]
        )
    finally:
        if owns_client:
            await client.close()

    return {
        c["id"]: r
        for c, r in zip(targets, results)
        if r is not None
    }
