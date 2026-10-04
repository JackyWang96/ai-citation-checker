"""LLM-generated APA 7th fix suggestions (opt-in, on demand).

Selects the citations worth sending, runs each through the fix loop in
`fix_graph`, and collects the results. This never runs during a normal check
— it is triggered by the user from the report page, only when an API key is
configured.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional
import anthropic
import app.config as cfg
from app.services.fix_graph import run_fix

logger = logging.getLogger(__name__)

# Issue types that a rewrite can plausibly fix. A "not_found" reference can't
# be corrected by reformatting, so those are left alone.
_FIXABLE_ISSUE_TYPES = frozenset({"format_violation", "field_mismatch"})


def fixable_citations(citations: list[dict]) -> list[dict]:
    """The citations a rewrite could help, in report order.

    Public because the caller has to know what it is about to pay for *before*
    paying — it claims those ids first, so a second concurrent request cannot
    buy the same work.
    """
    return [c for c in citations if _fixable(c)]


def _fixable(citation: dict) -> bool:
    """A reference citation carrying at least one rewrite-fixable issue."""
    if citation.get("kind") != "reference":
        return False
    # Already analysed — don't pay twice. The "declined" case covers a loop
    # that ran and produced nothing usable: without it such a citation looks
    # untouched and every click repeats the whole cost. Compared explicitly, so
    # a future status like "failed" cannot silently count as finished — it must
    # leave the citation retryable, and an open-ended truthy test would not.
    if citation.get("suggestion") or citation.get("suggestion_status") == "declined":
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
    """Run one citation through the fix loop.

    A single citation's failure must never fail the whole batch — the report
    is still valid without a suggestion.
    """
    async with sem:
        try:
            return await run_fix(citation, client)
        except Exception:
            logger.warning("fix loop failed for one citation", exc_info=True)
            return None


async def suggest_fixes(
    citations: list[dict],
    client: Optional[anthropic.AsyncAnthropic] = None,
) -> dict[str, dict]:
    """Map citation id -> suggestion payload for every fixable citation that
    produced a usable rewrite. `client` is injectable for tests; in production
    it defaults to a key-configured AsyncAnthropic."""
    targets = [c for c in citations if _fixable(c)]
    if not targets:
        return {}

    owns_client = client is None
    if owns_client:
        client = anthropic.AsyncAnthropic(
            api_key=cfg.ANTHROPIC_API_KEY,
            # Explicit, because analysis_lease_seconds() is derived from
            # these. The SDK default (600s, 2 retries) has no bound the
            # lease could be sized against.
            timeout=cfg.CLAUDE_TIMEOUT_SECONDS,
            max_retries=cfg.LLM_MAX_RETRIES,
        )

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
