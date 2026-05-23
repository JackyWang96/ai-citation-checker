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

# R008 — "Lastname, I. & Other" should be "Lastname, I., & Other"
# Matches initial-period-space-ampersand without comma in between.
_MISSING_COMMA_BEFORE_AMP_RE = re.compile(r'[A-Z]\.\s+&')

# R009 — "(YYYY) Title" should be "(YYYY). Title" (period after year parens)
_MISSING_PERIOD_AFTER_YEAR_RE = re.compile(r'\(\d{4}[a-z]?\)\s+[A-Z]')

# R010 — "Journal Name 12(3)" should be "Journal Name, 12(3)"
# Matches a letter followed by space, digit, "(", digit — no comma between.
_MISSING_COMMA_BEFORE_VOLUME_RE = re.compile(r'[A-Za-z]\s+\d+\(\d+\)')


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


def check_comma_before_ampersand(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R008: Multi-author lists need a comma before '&' — 'Smith, J., & Jones'
    not 'Smith, J. & Jones'. Only inspects the author section."""
    year_match = _YEAR_PARENS_RE.search(para.raw_text)
    author_section = para.raw_text[:year_match.start()] if year_match else para.raw_text
    if _MISSING_COMMA_BEFORE_AMP_RE.search(author_section):
        return _issue(
            "R008",
            "Multi-author list needs a comma before '&' (APA 7th R008)",
            detail="Use 'Lastname, I., & Other' not 'Lastname, I. & Other'",
        )
    return None


def check_period_after_year(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R009: Year parenthesis must be followed by a period — '(2020). Title'
    not '(2020) Title'."""
    if _MISSING_PERIOD_AFTER_YEAR_RE.search(para.raw_text):
        return _issue(
            "R009",
            "Missing period after year (APA 7th R009)",
            detail="Use '(2020). Title' not '(2020) Title'",
        )
    return None


def check_comma_before_volume(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R010: Journal title and volume number must be separated by a comma —
    'Journal Name, 12(3)' not 'Journal Name 12(3)'. Only checked after the
    year-parens so we don't false-trigger on text inside the title."""
    year_match = _YEAR_PARENS_RE.search(para.raw_text)
    after_year = para.raw_text[year_match.end():] if year_match else para.raw_text
    if _MISSING_COMMA_BEFORE_VOLUME_RE.search(after_year):
        return _issue(
            "R010",
            "Missing comma between journal name and volume (APA 7th R010)",
            detail="Use 'Journal Name, 12(3)' not 'Journal Name 12(3)'",
        )
    return None


# NOTE: R006 (check_hanging_indent) intentionally excluded — many real-world
# docs mix Normal/Bibliography styles for references, causing too many
# noisy warnings. Re-add to ALL_RULES if hanging-indent enforcement is wanted.
ALL_RULES = [
    check_author_format,
    check_year_parens,
    check_journal_italic,
    check_doi_format,
    check_author_separator,
    check_comma_before_ampersand,
    check_period_after_year,
    check_comma_before_volume,
]
