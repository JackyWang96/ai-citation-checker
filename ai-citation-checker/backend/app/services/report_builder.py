from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from app.models.schemas import Citation, CitationIssue, Report, TextRun
from app.services.citation_extractor import IntextCitation, ReferenceEntry
from app.services.verifier import VerifyResult, _norm, _strip_markup, _author_surname_match
from app.rules.apa7 import _journal_name, _is_chapter, _PP_OK_RE
from app.services.docx_parser import ReferenceParagraph
from app.services.apa_validator import validate_reference_paragraph
from rapidfuzz import fuzz

# Unicode-aware: supports accented surnames (Lemhöfer, Ferré, Łukasiewicz).
_INTEXT_FORMAT_RE = re.compile(
    r'^\([A-ZÀ-Ɏ][a-zA-ZÀ-ɏ\-]+(?:\s+et\s+al\.)?'
    r'(?:\s*[,&]\s*[A-ZÀ-Ɏ][a-zA-ZÀ-ɏ\-]+)*'
    r',\s*\d{4}[a-z]?(?:,\s*pp?\.\s*[\d\-]+)?\)$'
)

# Merged-entry detection: every reference has exactly one '(YYYY)' date, so
# two '(YYYY)' tokens mean two references glued into one entry (a lost
# paragraph break in Word). Field comparison against such a chimera produces
# nonsense warnings (journal name from ref #2 vs the Crossref record of ref #1).
#
# We deliberately do NOT key off DOI count: a stray/orphan DOI line (one
# reference's DOI wrapping onto its own line and getting merged into the
# *previous* entry) inflates the DOI count without there being two references —
# e.g. Horwitz (1986) picking up a trailing DOI belonging to another entry.
_YEAR_PARENS_RE = re.compile(r'\(\d{4}[a-z]?\)')


def _looks_like_merged_references(text: str) -> bool:
    return len(_YEAR_PARENS_RE.findall(text)) >= 2


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

        merged = _looks_like_merged_references(entry.raw_text)
        if merged:
            issues.append(CitationIssue(
                type="format_violation", severity="yellow", category="format",
                reason="Two references appear to be merged into one entry",
                detail="This entry contains multiple publication years. Check "
                       "for a missing paragraph break between the references, "
                       "or stray characters (e.g. a page number copied from a "
                       "PDF) at the start of the second reference, then "
                       "re-upload.",
            ))

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
            if not is_soft:
                # Show plain actionable guidance instead of the raw
                # per-database trail ('Crossref: score too low · …'), which
                # read like a system error. Grey literature (club-published
                # guidebooks etc.) legitimately isn't in any catalogue — the
                # user just needs to check it themselves.
                detail = "Please verify this reference manually"
            issues.append(CitationIssue(
                type="not_found",
                severity="yellow" if is_soft else "red",
                category="content",
                reason="Manual verification required" if is_soft else "Reference not found",
                detail=detail,
            ))
        else:
            # Field-level comparison — skipped for merged entries: the record
            # matches ref #1 but extracted fields may come from ref #2.
            if not merged:
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
    # Use the same lenient surname comparison as the verifier: Crossref often
    # stores only the last word of a multi-word surname (entry 'Van Vu' →
    # family 'Vu', given 'Duy Van') — strict equality falsely flagged those.
    if cand_author and not _author_surname_match(cand_author, entry.first_author_normalized):
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
    # Skip the title check when the entry is a chapter cite but the matched
    # record is the parent book (chapters often lack their own DOI, so DOI
    # lookup returns the book). The titles live at different levels —
    # entry.title = chapter title, cand title = book title — and reporting a
    # mismatch is misleading (the DOI itself is correct).
    entry_is_chapter = ". In " in entry.raw_text
    if not (entry_is_chapter and cand_type == "book"):
        score = fuzz.token_set_ratio(_norm(cand_title), entry.title_normalized)
        if score < 95:
            # Show user's original title (with case + punctuation) so they can
            # spot the actual difference — not the lowercased match-key.
            issues.append(CitationIssue(
                type="field_mismatch", severity="yellow", category="content",
                field="title", reason="Title does not match authoritative record",
                expected=cand_title, actual=entry.title_raw or entry.title_normalized,
            ))

    # Journal — compare the *extracted* journal name (not the whole reference
    # text: full-text comparison diluted the score and flagged legitimate
    # abbreviations like 'International Review of Applied Linguistics' vs the
    # official 'IRAL - International Review of Applied Linguistics in Language
    # Teaching'). Skip for book chapters: there `container-title` is the *book*
    # title, not a journal name. The chapter check looks at BOTH the Crossref
    # type and the cite's own format ('. In Editor (Ed.), …') — encyclopedia
    # entries are typed 'other' in Crossref, so type alone misses them.
    cand_journal = _strip_markup((c.get("container-title") or [""])[0])
    if (
        cand_journal
        and cand_type not in ("book-chapter", "reference-entry")
        and not _is_chapter(entry.raw_text)
    ):
        user_journal = _journal_name(entry.raw_text)
        if user_journal:
            # Normalise dashes to spaces so 'ITL-International Journal…'
            # matches 'ITL - International Journal…'.
            j_score = fuzz.token_set_ratio(
                _norm_dashes(cand_journal), _norm_dashes(user_journal)
            )
            if j_score < 90:
                issues.append(CitationIssue(
                    type="field_mismatch", severity="yellow", category="content",
                    field="journal", reason="Journal name does not match authoritative record",
                    expected=cand_journal,
                    actual=user_journal,
                ))
            elif (
                user_journal.lower() == cand_journal.lower()
                and user_journal != cand_journal
            ):
                # R018: same name, wrong capitalisation ('Language learning'
                # vs 'Language Learning') — APA 7 requires major words of a
                # journal name to be capitalised.
                issues.append(CitationIssue(
                    type="format_violation", severity="yellow", category="format",
                    rule_id="R018", field="journal",
                    reason="Journal name should be capitalised as in the authoritative record (APA 7th R018)",
                    expected=cand_journal,
                    actual=user_journal,
                ))

    # R019 — chapter cite without a page range, but the authoritative record
    # HAS one. Restricted to genuine edited-book chapters (type=book-chapter):
    # APA 7's own examples for online reference-work entries (encyclopedias,
    # typed 'other'/'reference-entry' in Crossref) omit page numbers, and
    # their `page` field is per-entry PDF pagination ('1-10'), so reminding
    # there would be noise.
    cand_page = (c.get("page") or "").strip()
    if (
        cand_page
        and cand_type == "book-chapter"
        and _is_chapter(entry.raw_text)
        and not _PP_OK_RE.search(entry.raw_text)
    ):
        issues.append(CitationIssue(
            type="format_violation", severity="yellow", category="format",
            rule_id="R019",
            reason="Chapter page range may be missing (APA 7th R019)",
            detail=f"The authoritative record lists pages {cand_page} — "
                   f"consider adding (pp. {cand_page}) after the book title.",
            expected=f"(pp. {cand_page})",
        ))

    # R020 — journal article cited without volume/issue/pages, but the
    # verified record HAS them. Data-driven like R019: only fires when the
    # record proves the metadata exists, so unusual publication forms never
    # false-trigger. Catches e.g. 'Gyllstad et al. (2024). Title. Journal.
    # Publisher.' passing as "APA format correct" with no vol/pages/DOI.
    if (
        cand_type == "journal-article"
        and (c.get("volume") or c.get("page"))
        and not _VOL_PAGES_PRESENT_RE.search(entry.raw_text)
    ):
        vol = c.get("volume") or "?"
        issue_no = c.get("issue")
        page = c.get("page") or ""
        expected = f"{vol}({issue_no}), {page}" if issue_no else f"{vol}, {page}"
        issues.append(CitationIssue(
            type="format_violation", severity="yellow", category="format",
            rule_id="R020",
            reason="Journal article may be missing volume/issue/page numbers (APA 7th R020)",
            detail=f"The authoritative record lists {expected.rstrip(', ')} — "
                   "journal-article references should include volume(issue) "
                   "and page range after the journal name.",
            expected=expected.rstrip(", "),
        ))

    return issues


# Volume/pages presence: '176(1)' or '45, 373-401' or a page range '1-43' or
# the modern article-number form 'System, 95, 102366' (volume, article no. —
# no page range). Used by R020 to detect journal cites with no numeric
# metadata at all.
_VOL_PAGES_PRESENT_RE = re.compile(
    r'\d+\s*\(\d+\)'                       # 176(1)
    r'|\d+\s*,\s*\d+\s*[-–—]\s*\d+'        # 45, 373-401
    r'|\d+\s*[-–—]\s*\d+'                  # 1-43
    r'|,\s*\d+\s*,\s*\d+'                  # , 95, 102366  (vol, article no.)
)


def _norm_dashes(s: str) -> str:
    return _norm(re.sub(r'[-–—]', ' ', s))


def _check_intext(intext: IntextCitation,
                  reference_entries: list[ReferenceEntry]) -> list[CitationIssue]:
    issues = []

    # Format check — parenthetical citations only. Narrative cites ('Smith
    # (2020) argued…') have their own shape and would all fail this regex.
    if not intext.narrative and not _INTEXT_FORMAT_RE.match(intext.raw_text):
        issues.append(CitationIssue(
            type="format_violation", severity="yellow", category="format",
            reason="In-text citation format does not conform to APA 7th (missing comma, parens, etc.)",
            actual=intext.raw_text,
        ))

    # R012: APA 7th requires 'et al.' for 3+ authors in in-text citations.
    if intext.n_authors >= 3 and not intext.has_etal:
        expected = (
            f"{intext.author} et al. ({intext.year})" if intext.narrative
            else f"({intext.author} et al., {intext.year})"
        )
        issues.append(CitationIssue(
            type="format_violation", severity="yellow", category="format",
            rule_id="R012",
            reason="In-text citation with 3+ authors should use 'et al.' (APA 7th R012)",
            expected=expected,
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
        expected = (
            f"{intext.author}{suffix} ({years_str})" if intext.narrative
            else f"({intext.author}{suffix}, {years_str})"
        )
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            rule_id="R013", field="year",
            reason="In-text year doesn't match reference list (APA 7th R013)",
            expected=expected,
            actual=intext.raw_text,
        ))
    else:
        issues.append(CitationIssue(
            type="orphan", severity="yellow", category="orphan",
            reason="No matching entry found in Reference List",
            detail=f"In-text cites ({intext.author}, {intext.year}) but no reference entry matches",
        ))

    return issues
