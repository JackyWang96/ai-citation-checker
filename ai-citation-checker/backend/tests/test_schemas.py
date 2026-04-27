from app.models.schemas import CitationIssue, Citation, Report
from datetime import datetime, timezone

def test_citation_issue_requires_reason():
    issue = CitationIssue(
        type="not_found",
        severity="red",
        category="content",
        reason="文献不存在",
    )
    assert issue.reason == "文献不存在"
    assert issue.detail is None
    assert issue.expected is None

def test_citation_status_defaults():
    c = Citation(
        id="c1",
        kind="reference",
        raw_text="Smith, J. (2020). AI. Journal, 1(1).",
        char_start=0,
        char_end=40,
        status="pass",
        issues=[],
    )
    assert c.verified_reference_id is None

def test_report_summary_shape():
    now = datetime.now(timezone.utc)
    r = Report(
        id="abc",
        filename="essay.docx",
        created_at=now,
        expires_at=now,
        full_text="...",
        citations=[],
        summary={"total": 0, "pass": 0, "warning": 0, "error": 0},
    )
    assert r.summary["total"] == 0
