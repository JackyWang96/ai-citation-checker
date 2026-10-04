"""On-demand LLM fix-suggestion endpoint.

Separate from /api/check so a normal upload never incurs LLM cost. The user
opts in from the report page; suggestions are written back into the stored
report so a shared link keeps them and a re-run doesn't pay twice.
"""
import json
import uuid

import app.config as cfg
from fastapi import APIRouter, Response

from app.services.fix_suggester import fixable_citations, suggest_fixes
from app.storage.db import (
    claim_citations,
    get_report,
    merge_citation_fields,
    release_claims,
)

router = APIRouter()


@router.post("/api/analyze/{report_id}")
async def analyze_report(report_id: str, response: Response):
    if not cfg.LLM_ENABLED:
        response.status_code = 503
        return {"detail": "AI analysis is not configured on this server"}

    row = await get_report(cfg.DB_PATH, report_id)
    if row is None:
        response.status_code = 410
        return {"detail": "Report expired or not found"}

    report = json.loads(row["report_json"])
    targets = fixable_citations(report.get("citations", []))
    if not targets:
        return report

    # Claim before paying. Two tabs hitting this endpoint together would
    # otherwise both see the citations as unanalysed and both be charged for
    # the same rewrites; whoever loses the claim simply returns what it read.
    owner = uuid.uuid4().hex
    won = await claim_citations(
        cfg.DB_PATH, report_id, [c["id"] for c in targets], owner,
        cfg.ANALYSIS_LEASE_SECONDS,
    )
    claimed = [c for c in targets if c["id"] in won]
    if not claimed:
        return report

    try:
        suggestions = await suggest_fixes(claimed)
    except Exception:
        # Release rather than wait out the lease: nothing was persisted, so the
        # user should be able to retry immediately.
        await release_claims(cfg.DB_PATH, report_id, owner)
        raise

    if suggestions:
        # Merged under the write lock rather than overwriting the snapshot this
        # request read, and gated on the lease still being ours, so neither a
        # concurrent analyse nor a lease takeover can be clobbered.
        merged = await merge_citation_fields(
            cfg.DB_PATH, report_id, suggestions, owner=owner
        )
        if merged is not None:
            return merged

    await release_claims(cfg.DB_PATH, report_id, owner)
    return report
