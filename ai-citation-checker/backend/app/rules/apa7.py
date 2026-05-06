from __future__ import annotations
import re
from typing import Optional
from app.models.schemas import CitationIssue
from app.services.docx_parser import ReferenceParagraph

_AUTHOR_FORMAT_RE = re.compile(r'^[A-Z][a-zA-Z\-]+,\s+[A-Z]\.')
_YEAR_PARENS_RE = re.compile(r'\(\d{4}[a-z]?\)')
_DOI_URL_RE = re.compile(r'https?://doi\.org/')
_DOI_BARE_RE = re.compile(r'\bdoi:\s*10\.')
_AND_RE = re.compile(r'\band\b', re.IGNORECASE)
_AMPERSAND_MULTI_RE = re.compile(r'[A-Z][a-zA-Z]+,\s+[A-Z]\.\s*,')


def _issue(rule_id: str, reason: str, detail: str | None = None) -> CitationIssue:
    return CitationIssue(
        type="format_violation",
        severity="yellow",
        category="format",
        reason=reason,
        detail=detail,
        rule_id=rule_id,
    )


def check_author_format(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R001: First author must be Last, F. M. format."""
    if not _AUTHOR_FORMAT_RE.match(para.raw_text):
        return _issue("R001", "Author format should be Last, F. M. (APA 7th R001)")
    return None


def check_year_parens(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R002: Year must appear in parentheses."""
    if not _YEAR_PARENS_RE.search(para.raw_text):
        return _issue("R002", "Year should be in parentheses, e.g. (2020) (APA 7th R002)")
    return None


def check_journal_italic(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R003: Journal title must be italicised."""
    has_italic = any(is_italic for _, is_italic in para.runs)
    if not has_italic:
        return _issue("R003", "Journal name should be in italics (APA 7th R003)")
    return None


def check_hanging_indent(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R006: Reference paragraph must use hanging indent."""
    if not para.has_hanging_indent:
        return _issue("R006", "Reference paragraph should use hanging indent (APA 7th R006)")
    return None


def check_doi_format(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R005: DOI must use https://doi.org/ format."""
    if _DOI_BARE_RE.search(para.raw_text):
        return _issue("R005", "DOI should use https://doi.org/... format, not doi:... (APA 7th R005)")
    return None


def check_author_separator(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R007: Multiple authors joined by ', &' not 'and'."""
    # Only check the author section (text before the year parenthesis)
    year_match = _YEAR_PARENS_RE.search(para.raw_text)
    author_section = para.raw_text[:year_match.start()] if year_match else para.raw_text
    has_multiple_authors = bool(re.search(r'[A-Z][a-zA-Z]+,\s+[A-Z]\.', author_section))
    if has_multiple_authors and _AND_RE.search(author_section):
        return _issue("R007", "Multiple authors should use ', & ' not 'and' (APA 7th R007)")
    return None


ALL_RULES = [
    check_author_format,
    check_year_parens,
    check_journal_italic,
    check_doi_format,
    check_hanging_indent,
    check_author_separator,
]
