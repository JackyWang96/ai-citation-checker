"""Stage 2 — on-demand LLM fix-suggestion endpoint.

Separate from /api/check so a normal upload never incurs LLM cost. The user
opts in from the report page; suggestions are written back into the stored
report so a shared link keeps them and a re-run doesn't pay twice.
"""
import json
import app.config as cfg
from fastapi import APIRouter, Response
from app.storage.db import get_report, update_report
from app.services.fix_suggester import suggest_fixes

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
    suggestions = await suggest_fixes(report.get("citations", []))

    if suggestions:
        for c in report["citations"]:
            fix = suggestions.get(c["id"])
            if fix:
                c["suggestion"] = fix["suggestion"]
                c["suggestion_explanation"] = fix["suggestion_explanation"]
                c["suggestion_verified"] = fix["suggestion_verified"]
                c["suggestion_rule_basis"] = fix["suggestion_rule_basis"]
                c["suggestion_validation"] = fix["suggestion_validation"]
        await update_report(cfg.DB_PATH, report_id, json.dumps(report))

    return report
