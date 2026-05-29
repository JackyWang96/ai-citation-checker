from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from app.models.schemas import Citation, CitationIssue, Report, TextRun
from app.services.citation_extractor import IntextCitation, ReferenceEntry
from app.services.verifier import VerifyResult, _norm, _strip_markup
from app.services.docx_parser import ReferenceParagraph
from app.services.apa_validator import validate_reference_paragraph
from rapidfuzz import fuzz

# Unicode-aware: supports accented surnames (Lemhöfer, Ferré, Łukasiewicz).
_INTEXT_FORMAT_RE = re.compile(
    r'^\([A-ZÀ-Ɏ][a-zA-ZÀ-ɏ\-]+(?:\s+et\s+al\.)?'
    r'(?:\s*[,&]\s*[A-ZÀ-Ɏ][a-zA-ZÀ-ɏ\-]+)*'
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
            detail = vr.not_found_reason or "no match in Crossref or OpenAlex"
            # Soft-warning categories: references that legitimately aren't in
            # academic databases (software, web pages, self-hosted conference
            # proceedings). Downgrade to yellow so users aren't alarmed.
            soft_markers = (
                "web/organisation",
                "software citation",
                "conference proceedings",
            )
            is_soft = any(m in (vr.not_found_reason or "") for m in soft_markers)
            issues.append(CitationIssue(
                type="not_found",
                severity="yellow" if is_soft else "red",
                category="content",
                reason="Manual verification required" if is_soft else "Reference not found",
                detail=detail,
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
        # Find char position of this reference in full_text. If raw_text was
        # merged from multiple paragraphs, full_text may still contain newlines
        # and the find() will fail — fall back to the end of full_text so the
        # reference still renders in the References panel but doesn't truncate
        # the body text (which uses min(char_start) as the body/refs boundary).
        start = full_text.find(entry.raw_text)
        if start < 0:
            start = len(full_text)
            end = start
        else:
            end = start + len(entry.raw_text)

        citations.append(Citation(
            id=f"r{counter}",
            kind="reference",
            raw_text=entry.raw_text,
            char_start=start,
            char_end=end,
            status=status,
            issues=issues,
            verified_reference_id=vr.verified_reference_id,
            runs=[TextRun(text=t, italic=ital) for t, ital in para.runs] or None,
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
    cand_type = (c.get("type") or "").lower()

    # Author — normalize Unicode hyphens (U+2010 etc.) to ASCII before comparing
    cand_author = ((c.get("author") or [{}])[0].get("family") or "").lower()
    cand_author = cand_author.replace("‐", "-").replace("‑", "-")
    if cand_author and cand_author != entry.first_author_normalized:
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            field="author",
            reason="Author name mismatch",
            expected=cand_author.title(),
            actual=entry.first_author_normalized.title(),
        ))

    # Year — prefer print publication year over online-first date
    print_parts = (c.get("published-print") or {}).get("date-parts") or []
    online_parts = (c.get("published") or {}).get("date-parts") or []
    cand_year = (print_parts[0][0] if print_parts else None) or (online_parts[0][0] if online_parts else 0)
    if cand_year and abs(cand_year - entry.year) > 1:
        # Books commonly have multiple editions/reprints — the Crossref record
        # may be a later/different edition. Soften the warning so users don't
        # treat it as a hard error.
        is_book = cand_type in ("book", "monograph", "edited-book", "reference-book")
        if is_book:
            issues.append(CitationIssue(
                type="field_mismatch", severity="yellow", category="content",
                field="year",
                reason="Possible alternative publication year",
                detail="This work appears to have multiple editions or reprints — "
                       "verify whether your cited year matches the edition you used.",
                expected=str(cand_year), actual=str(entry.year),
            ))
        else:
            issues.append(CitationIssue(
                type="field_mismatch", severity="yellow", category="content",
                field="year", reason="Year mismatch",
                expected=str(cand_year), actual=str(entry.year),
            ))

    # Title — strip embedded JATS/HTML markup (e.g. "<b>lmerTest</b>") so it
    # neither pollutes the fuzzy score nor leaks into the displayed expected.
    cand_title = _strip_markup((c.get("title") or [""])[0])
    score = fuzz.token_set_ratio(_norm(cand_title), entry.title_normalized)
    if score < 95:
        # Show user's original title (with case + punctuation) so they can
        # spot the actual difference — not the lowercased match-key.
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            field="title", reason="Title does not match authoritative record",
            expected=cand_title, actual=entry.title_raw or entry.title_normalized,
        ))

    # Journal — only flag if a journal-like token is present but doesn't match.
    # Skip for book chapters: there `container-title` is the *book* title, not a
    # journal name. Generic chapter titles also frequently resolve to a different
    # same-named book, which produced false "journal mismatch" warnings.
    cand_journal = _strip_markup((c.get("container-title") or [""])[0])
    if cand_journal and cand_type not in ("book-chapter", "reference-entry"):
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

    # R012: APA 7th requires 'et al.' for 3+ authors in in-text citations.
    if intext.n_authors >= 3 and not intext.has_etal:
        issues.append(CitationIssue(
            type="format_violation", severity="yellow", category="format",
            rule_id="R012",
            reason="In-text citation with 3+ authors should use 'et al.' (APA 7th R012)",
            expected=f"({intext.author} et al., {intext.year})",
            actual=intext.raw_text,
        ))

    # Match against reference list. Distinguish three cases for better UX:
    #   1. Exact match (first author + year + optional second author) → no issue
    #   2. Same first author but different year → R013 year mismatch
    #   3. No reference by this first author → orphan
    intext_first = intext.author.lower()
    intext_second = intext.second_author.lower()

    same_author_refs = [
        e for e in reference_entries
        if e.first_author_normalized == intext_first
    ]
    same_year_refs = [e for e in same_author_refs if e.year == intext.year]

    def _second_author_ok(e: ReferenceEntry) -> bool:
        return (
            not intext_second
            or intext.has_etal
            or not e.second_author_normalized
            or e.second_author_normalized == intext_second
        )

    if any(_second_author_ok(e) for e in same_year_refs):
        return issues  # exact match, no warning needed

    if same_author_refs and not same_year_refs:
        # Same first author exists in references but the year differs — most
        # likely a typo in the in-text citation (e.g. cited 2025 but ref is 2024).
        years = sorted({e.year for e in same_author_refs if e.year > 0})
        years_str = (
            str(years[0]) if len(years) == 1
            else " or ".join(str(y) for y in years)
        )
        suffix = " et al." if intext.has_etal else ""
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            rule_id="R013", field="year",
            reason="In-text year doesn't match reference list (APA 7th R013)",
            expected=f"({intext.author}{suffix}, {years_str})",
            actual=intext.raw_text,
        ))
    else:
        issues.append(CitationIssue(
            type="orphan", severity="yellow", category="orphan",
            reason="No matching entry found in Reference List",
            detail=f"In-text cites ({intext.author}, {intext.year}) but no reference entry matches",
        ))

    return issues
