"""On-demand LLM fix-suggestion endpoint.

Separate from /api/check so a normal upload never incurs LLM cost. The user
opts in from the report page; suggestions are written back into the stored
report so a shared link keeps them and a re-run doesn't pay twice.
"""
import json
import uuid

import app.config as cfg
from fastapi import APIRouter, Response

from app.services.fix_suggester import fixable_citations, settle_fixes
from app.storage.db import (
    claim_citations,
    fail_exhausted_citations,
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
    #
    # This narrows the double-billing window; it cannot close it. Payment
    # happens outside any database transaction, so a crash after paying and
    # before committing still leaves the work to be redone.
    owner = uuid.uuid4().hex
    target_ids = [c["id"] for c in targets]
    won = await claim_citations(
        cfg.DB_PATH, report_id, target_ids, owner,
        cfg.analysis_lease_seconds(len(targets)),
    )

    # A citation we wanted but could not win may simply be in someone else's
    # hands — or its budget may be spent by a worker that died on its last
    # attempt, so nobody ever marked it failed. Settle those now; otherwise
    # the page keeps offering a button that can never win a claim.
    lost = [cid for cid in target_ids if cid not in won]
    swept = await fail_exhausted_citations(cfg.DB_PATH, report_id, lost)
    if swept is not None:
        report = swept
    if not won:
        return report

    # Re-check eligibility now that the lease is ours. `targets` came from the
    # snapshot read before claiming: if another request finished these
    # citations and released them in between, the claim succeeds anyway and we
    # would pay a second time and overwrite its result. Once our lease is in
    # place nobody else can start them, so this read is the safe one.
    latest_row = await get_report(cfg.DB_PATH, report_id)
    if latest_row is None:
        # Nothing has been paid for yet, so every attempt claimed above is
        # refunded — otherwise a vanished report would eat into the budget.
        await release_claims(cfg.DB_PATH, report_id, owner, refund=set(won))
        response.status_code = 410
        return {"detail": "Report expired or not found"}
    report = json.loads(latest_row["report_json"])
    still_fixable = {c["id"] for c in fixable_citations(report.get("citations", []))}
    claimed = [c for c in report.get("citations", [])
               if c["id"] in won and c["id"] in still_fixable]
    stale = [cid for cid in won if cid not in still_fixable]
    if stale:
        # Already finished by someone else; we never paid for these.
        await release_claims(cfg.DB_PATH, report_id, owner, stale, refund=set(stale))
    if not claimed:
        return report

    try:
        settlement = await settle_fixes(claimed)
    except Exception:
        # Release so the user can retry immediately. No refund: this failed
        # partway through, and some of it may already have been charged.
        await release_claims(cfg.DB_PATH, report_id, owner)
        raise

    # Settle every claimed citation in one transaction — written, refunded,
    # released, or given up as failed once its budget is spent. Merged under
    # the write lock rather than over the snapshot this request read, and
    # skipping any citation another worker holds or has finished, so a
    # takeover's result is never overwritten.
    merged = await merge_citation_fields(
        cfg.DB_PATH, report_id, settlement.results, owner=owner,
        refund=settlement.unbilled,
    )
    if merged is None:
        # The report was cleaned up while we were analysing. The results have
        # nowhere to go; answering 200 with the old snapshot would tell the
        # client the request succeeded and show it nothing.
        response.status_code = 410
        return {"detail": "Report expired or not found"}
    return merged
