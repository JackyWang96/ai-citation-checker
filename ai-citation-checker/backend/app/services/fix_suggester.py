"""LLM-generated APA 7th fix suggestions (opt-in, on demand).

Selects the citations worth sending, runs each through the fix loop in
`fix_graph`, and collects the results. This never runs during a normal check
— it is triggered by the user from the report page, only when an API key is
configured.
"""
from __future__ import annotations
import asyncio
import logging
from typing import NamedTuple, Optional
import anthropic
import app.config as cfg
from app.services.fix_graph import FixOutcome, run_fix_outcome

logger = logging.getLogger(__name__)

# Issue types that a rewrite can plausibly fix. A "not_found" reference can't
# be corrected by reformatting, so those are left alone.
_FIXABLE_ISSUE_TYPES = frozenset({"format_violation", "field_mismatch"})

# Outcomes that end a citation's analysis for the life of the report.
# "declined": the model judged the reference already correct.
# "failed": it was charged ANALYSIS_MAX_BILLED_ATTEMPTS times and never
# produced anything usable — retrying again would only pay again.
TERMINAL_STATUSES = frozenset({"declined", "failed"})


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
    # Already analysed — don't pay twice. A terminal status covers a loop that
    # ran and will not be retried: without it such a citation looks untouched
    # and every click repeats the whole cost. Matched against an explicit set,
    # so a status added later stays retryable until someone decides otherwise;
    # an open-ended truthy test would silently treat it as finished.
    if citation.get("suggestion") or citation.get("suggestion_status") in TERMINAL_STATUSES:
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


class Settlement(NamedTuple):
    """The batch result, split the way the caller has to settle it.

    `results` are written to the report. `unbilled` are citations that
    produced nothing and cost nothing — their claimed attempt is refunded.
    Every other claimed citation that is in neither counts as a charged
    attempt, which is the safe default for anything uncertain.
    """
    results: dict[str, dict]
    unbilled: set[str]


async def _settle_one(client: anthropic.AsyncAnthropic, citation: dict,
                      sem: asyncio.Semaphore) -> FixOutcome:
    """Run one citation through the fix loop.

    A single citation's failure must never fail the whole batch — the report
    is still valid without a suggestion.
    """
    async with sem:
        try:
            # The hard bound the analysis lease is sized from. SDK timeouts
            # are per phase and cannot be relied on to end a call; this cancels
            # it. A citation that runs out stays unanalysed and retryable.
            return await asyncio.wait_for(
                run_fix_outcome(citation, client),
                timeout=cfg.ANALYSIS_CITATION_DEADLINE_SECONDS,
            )
        except Exception:
            # Hit the deadline, or failed in a way the loop did not classify.
            # Either could have happened after a response was charged, so
            # assume it was: refunding on a guess would let the retry budget
            # be spent without limit.
            logger.warning("fix loop failed for one citation", exc_info=True)
            return FixOutcome(None, billed=True)


async def settle_fixes(
    citations: list[dict],
    client: Optional[anthropic.AsyncAnthropic] = None,
) -> Settlement:
    """Analyse every fixable citation and report what each one cost.
    `client` is injectable for tests; in production it defaults to a
    key-configured AsyncAnthropic."""
    targets = [c for c in citations if _fixable(c)]
    if not targets:
        return Settlement({}, set())

    owns_client = client is None
    if owns_client:
        client = anthropic.AsyncAnthropic(
            api_key=cfg.ANTHROPIC_API_KEY,
            # Explicit so a call does not burn its concurrency slot for the SDK
            # default of half an hour before the per-citation deadline cuts it.
            # These do not bound the lease — the deadline does.
            timeout=cfg.CLAUDE_TIMEOUT_SECONDS,
            max_retries=cfg.LLM_MAX_RETRIES,
        )

    sem = asyncio.Semaphore(cfg.LLM_CONCURRENCY)
    try:
        outcomes = await asyncio.gather(
            *[_settle_one(client, c, sem) for c in targets]
        )
    finally:
        if owns_client:
            await client.close()

    return Settlement(
        results={c["id"]: o.payload for c, o in zip(targets, outcomes)
                 if o.payload is not None},
        unbilled={c["id"] for c, o in zip(targets, outcomes)
                  if o.payload is None and not o.billed},
    )


async def suggest_fixes(
    citations: list[dict],
    client: Optional[anthropic.AsyncAnthropic] = None,
) -> dict[str, dict]:
    """Map citation id -> payload for every citation that produced one."""
    return (await settle_fixes(citations, client)).results
