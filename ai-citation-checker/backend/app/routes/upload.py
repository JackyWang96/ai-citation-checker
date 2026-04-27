import uuid
import app.config as cfg
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.services.docx_parser import parse_docx
from app.services.citation_extractor import extract_intext_citations, parse_reference_entries
from app.services.verifier import verify_all
from app.services.report_builder import build_report
from app.storage.db import save_report

router = APIRouter()


@router.post("/api/check")
async def check_essay(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    try:
        parsed = parse_docx(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    intext = extract_intext_citations(parsed.body_text)
    ref_entries = parse_reference_entries(
        [p.raw_text for p in parsed.reference_paragraphs]
    )
    verify_results = await verify_all(ref_entries, cfg.DB_PATH)

    report_id = str(uuid.uuid4())
    report = build_report(
        report_id=report_id,
        filename=file.filename,
        full_text=parsed.full_text,
        intext_citations=intext,
        reference_entries=ref_entries,
        reference_paragraphs=parsed.reference_paragraphs,
        verify_results=verify_results,
    )

    await save_report(cfg.DB_PATH, report_id, report.model_dump_json(), file.filename)
    return {"report_id": report_id, "url": f"/r/{report_id}"}
