from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from app.models.schemas import Citation, CitationIssue, Report
from app.services.citation_extractor import IntextCitation, ReferenceEntry
from app.services.verifier import VerifyResult, _norm
from app.services.docx_parser import ReferenceParagraph
from app.services.apa_validator import validate_reference_paragraph
from rapidfuzz import fuzz

_INTEXT_FORMAT_RE = re.compile(
    r'^\([A-Z][a-zA-Z\-]+(?:\s+et\s+al\.)?'
    r'(?:\s*[,&]\s*[A-Z][a-zA-Z\-]+)*'
    r',\s*\d{4}[a-z]?(?:,\s*pp?\.\s*[\d\-]+)?\)$'
)


def build_report(
    report_id: str,
    filename: str,
    full_text: str,
    intext_citations: list[IntextCitation],
    reference_entries: list[ReferenceEntry],
    reference_paragraphs: list[ReferenceParagraph],
    verify_results: list[VerifyResult],
) -> Report:
    citations: list[Citation] = []
    counter = 0

    # Build reference citations
    for entry, para, vr in zip(reference_entries, reference_paragraphs, verify_results):
        counter += 1
        issues: list[CitationIssue] = []

        if not vr.found:
            issues.append(CitationIssue(
                type="not_found", severity="red", category="content",
                reason="Reference not found: no match in Crossref or OpenAlex",
            ))
        else:
            # Field-level comparison
            issues.extend(_compare_fields(entry, vr))
            # APA format check
            issues.extend(validate_reference_paragraph(para))
            # Ambiguity
            if vr.ambiguous:
                issues.append(CitationIssue(
                    type="ambiguous", severity="yellow", category="ambiguous",
                    reason="Multiple papers found for same author+year — verify you cited the right one",
                    detail="Other titles: " + ", ".join(vr.other_titles),
                ))

        status = _status(issues)
        # Find char position of this reference in full_text
        start = full_text.find(entry.raw_text)
        end = start + len(entry.raw_text) if start >= 0 else 0

        citations.append(Citation(
            id=f"r{counter}",
            kind="reference",
            raw_text=entry.raw_text,
            char_start=max(start, 0),
            char_end=max(end, 0),
            status=status,
            issues=issues,
            verified_reference_id=vr.verified_reference_id,
        ))

    # Build in-text citations
    for intext in intext_citations:
        counter += 1
        issues = _check_intext(intext, reference_entries)
        citations.append(Citation(
            id=f"i{counter}",
            kind="intext",
            raw_text=intext.raw_text,
            char_start=intext.char_start,
            char_end=intext.char_end,
            status=_status(issues),
            issues=issues,
        ))

    now = datetime.now(timezone.utc)
    total = len(citations)
    pass_ = sum(1 for c in citations if c.status == "pass")
    warn = sum(1 for c in citations if c.status == "warning")
    err = sum(1 for c in citations if c.status == "error")

    return Report(
        id=report_id,
        filename=filename,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        full_text=full_text,
        citations=citations,
        summary={"total": total, "pass": pass_, "warning": warn, "error": err},
    )


def _status(issues: list[CitationIssue]) -> str:
    if any(i.severity == "red" for i in issues):
        return "error"
    if issues:
        return "warning"
    return "pass"


def _compare_fields(entry: ReferenceEntry, vr: VerifyResult) -> list[CitationIssue]:
    if not vr.canonical:
        return []
    issues = []
    c = vr.canonical

    # Author
    cand_author = ((c.get("author") or [{}])[0].get("family") or "").lower()
    if cand_author and cand_author != entry.first_author_normalized:
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            field="author",
            reason="Author name mismatch",
            expected=cand_author.title(),
            actual=entry.first_author_normalized.title(),
        ))

    # Year
    cand_year = ((c.get("published") or {}).get("date-parts") or [[0]])[0][0]
    if cand_year and cand_year != entry.year:
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            field="year", reason="Year mismatch",
            expected=str(cand_year), actual=str(entry.year),
        ))

    # Title
    cand_title = (c.get("title") or [""])[0]
    score = fuzz.token_set_ratio(_norm(cand_title), entry.title_normalized)
    if score < 95:
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            field="title", reason="Title does not match authoritative record",
            expected=cand_title, actual=entry.title_normalized,
        ))

    # Journal — only flag if a journal-like token is present but doesn't match
    cand_journal = (c.get("container-title") or [""])[0]
    if cand_journal:
        j_score = fuzz.token_set_ratio(_norm(cand_journal), _norm(entry.raw_text))
        # Score in [50, 90) means a journal name is present but wrong.
        # Score < 50 means the journal is simply absent (let APA format rules catch that).
        if 50 <= j_score < 90:
            issues.append(CitationIssue(
                type="field_mismatch", severity="yellow", category="content",
                field="journal", reason="Journal name does not match authoritative record",
                expected=cand_journal,
            ))

    return issues


def _check_intext(intext: IntextCitation,
                  reference_entries: list[ReferenceEntry]) -> list[CitationIssue]:
    issues = []

    # Format check
    if not _INTEXT_FORMAT_RE.match(intext.raw_text):
        issues.append(CitationIssue(
            type="format_violation", severity="yellow", category="format",
            reason="In-text citation format does not conform to APA 7th (missing comma, parens, etc.)",
            actual=intext.raw_text,
        ))

    # Orphan check
    matched = any(
        e.first_author_normalized == intext.author.lower() and e.year == intext.year
        for e in reference_entries
    )
    if not matched:
        issues.append(CitationIssue(
            type="orphan", severity="yellow", category="orphan",
            reason="No matching entry found in Reference List",
            detail=f"In-text cites ({intext.author}, {intext.year}) but no reference entry matches",
        ))

    return issues
