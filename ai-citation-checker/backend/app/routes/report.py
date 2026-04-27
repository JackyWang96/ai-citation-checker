import json
import app.config as cfg
from fastapi import APIRouter, Response
from app.storage.db import get_report

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/api/report/{report_id}")
async def get_report_by_id(report_id: str, response: Response):
    row = await get_report(cfg.DB_PATH, report_id)
    if row is None:
        response.status_code = 410
        return {"detail": "Report expired or not found"}
    return json.loads(row["report_json"])
