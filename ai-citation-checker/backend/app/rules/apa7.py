from __future__ import annotations
import re
from typing import Optional
from app.models.schemas import CitationIssue
from app.services.docx_parser import ReferenceParagraph

# Surname character class — uppercase Latin/extended Latin (À-Ɏ covers French,
# German, Spanish, Polish, Czech, Nordic, etc.) followed by any word char,
# hyphen, en/em dash, or apostrophe (for names like O'Brien, Pekarek-Doehler).
_AUTHOR_FORMAT_RE = re.compile(r'^[A-ZÀ-Ɏ][\w\-‐‑\']+,\s+[A-ZÀ-Ɏ]\.')
_YEAR_PARENS_RE = re.compile(r'\(\d{4}[a-z]?\)')
_DOI_URL_RE = re.compile(r'https?://doi\.org/')
_DOI_BARE_RE = re.compile(r'\bdoi:\s*10\.')
_AND_RE = re.compile(r'\band\b', re.IGNORECASE)
_AMPERSAND_MULTI_RE = re.compile(r'[A-ZÀ-Ɏ][\w\-‐‑\']+,\s+[A-Z]\.\s*,')

# R008 — "Lastname, I. & Other" should be "Lastname, I., & Other"
# Matches initial-period-space-ampersand without comma in between.
_MISSING_COMMA_BEFORE_AMP_RE = re.compile(r'[A-ZÀ-Ɏ]\.\s+&')

# R009 — "(YYYY) Title" should be "(YYYY). Title" (period after year parens)
_MISSING_PERIOD_AFTER_YEAR_RE = re.compile(r'\(\d{4}[a-z]?\)\s+[A-ZÀ-Ɏ]')

# R010 — "Journal Name 12(3)" should be "Journal Name, 12(3)"
# Matches a letter followed by space, digit, "(", digit — no comma between.
_MISSING_COMMA_BEFORE_VOLUME_RE = re.compile(r'[A-Za-z]\s+\d+\(\d+\)')

# R011 — hyphen with adjacent space in compound words, e.g. "meta- analysis"
# or "meta -analysis". Restricted to letter-hyphen-space-letter (or mirrored)
# so page ranges like "1- 10" or "pp. 1 - 10" don't false-trigger.
_HYPHEN_SPACE_RE = re.compile(r'[A-Za-zÀ-ɏ](?:-\s+|\s+-)[A-Za-zÀ-ɏ]')

# --- Book-chapter detection (R014–R016) ---------------------------------
# An edited-book chapter looks like:
#   Author, A. (Year). Chapter title. In E. E. Editor (Eds.), Book title
#   (pp. 123–145). Publisher.
# We detect the ". In <Capital>" source marker, then require either a page
# group or an editors group so journal titles containing "In" don't trigger.
_CHAPTER_IN_RE = re.compile(r'\.\s+In\s+[A-Z0-9À-Ɏ]')
_EDITORS_RE = re.compile(r'\(\s*[Ee]ds?\.\s*\)')
_PAGE_GROUP_RE = re.compile(r'\(\s*pp?\.\s*\d|\(\s*\d+\s*[-–—]\s*\d+\s*\)')
# Correct chapter page format: "(pp. 351" or "(p. 5".
_PP_OK_RE = re.compile(r'\(\s*pp?\.\s*\d')
# Bare page range with no pp./p. prefix: "(351–381)".
_BARE_PAGES_RE = re.compile(r'\(\s*\d+\s*[-–—]\s*\d+\s*\)')
# Editor section written in author order ("Surname, I.") instead of "I. Surname".
_EDITOR_AUTHOR_ORDER_RE = re.compile(r'[A-ZÀ-Ɏ][\w\-‐‑\']+,\s+[A-Z]\.')


def _is_chapter(text: str) -> bool:
    if not _CHAPTER_IN_RE.search(text):
        return False
    return bool(_PAGE_GROUP_RE.search(text) or _EDITORS_RE.search(text))


# R003 journal detection: a journal article ends with
# "Journal Name, Volume(Issue), start–end" (issue optional). We locate that
# trailing ", NN(NN), NN–NN" marker and take the text just before it as the
# journal name. This lets R003 fire ONLY when a journal actually exists —
# software, datasets, and web pages have no such structure, so they no longer
# get a bogus "journal name should be in italics" warning.
_JOURNAL_STRUCT_RE = re.compile(
    r',\s*\d+\s*(?:\(\s*\d+[a-zA-Z]?\s*\))?\s*,\s*\d+\s*[-–—]\s*\d+'
)


def _norm_text(s: str) -> str:
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', '', s.lower())).strip()


def _journal_name(text: str) -> Optional[str]:
    """Return the journal name if the reference has journal-article structure,
    else None. The name is the segment between the end of the article title
    ('. ') and the volume/issue marker.

    NBSP (U+00A0) is normalised to a regular space first: Word often inserts
    non-breaking spaces between sentences ("proficiency.\\xa0Behavior") and
    the literal '. ' lookup below would miss them, swallowing the article
    title into the extracted journal name."""
    text = text.replace("\xa0", " ")
    m = _JOURNAL_STRUCT_RE.search(text)
    if not m:
        return None
    before = text[:m.start()].rstrip().rstrip(',').rstrip()
    idx = before.rfind('. ')
    name = (before[idx + 2:] if idx != -1 else before).strip()
    return name or None


def _italic_text(runs: list[tuple[str, bool]]) -> str:
    # Join with a space, not "": Word often splits italic spans at whitespace
    # (only the words are flagged italic, intervening spaces are not). Without
    # a separator the words glue together — "Behavior Research Methods" becomes
    # "BehaviorResearchMethods" and the journal-name substring check fails.
    # _norm_text collapses any extra whitespace afterwards, so this is safe
    # when italic runs already include their own spaces.
    return " ".join(t for t, is_italic in runs if is_italic)


def _issue(
    rule_id: str,
    reason: str,
    detail: str | None = None,
    expected: str | None = None,
    actual: str | None = None,
) -> CitationIssue:
    return CitationIssue(
        type="format_violation",
        severity="yellow",
        category="format",
        reason=reason,
        detail=detail,
        expected=expected,
        actual=actual,
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
    """R003: the element that APA requires in italics must actually be italic.

    For a chapter that's the book title. For a journal article we first detect
    the journal name (text before the 'Vol(Issue), pages' marker) and only then
    check whether *that* name is italicised. References with no journal — e.g.
    software ('(R package version 3.1-3)'), datasets, web pages — have no such
    structure, so they no longer trigger a bogus 'journal name' warning."""
    if _is_chapter(para.raw_text):
        if any(is_italic for _, is_italic in para.runs):
            return None
        return _issue("R003", "Book title should be in italics (APA 7th R003)")

    journal = _journal_name(para.raw_text)
    if journal is None:
        return None  # no journal name present → journal-italic rule doesn't apply
    norm_journal = _norm_text(journal)
    if norm_journal and norm_journal in _norm_text(_italic_text(para.runs)):
        return None  # the journal name is italicised
    return _issue("R003", "Journal name should be in italics (APA 7th R003)")


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
    has_multiple_authors = bool(re.search(r'[A-ZÀ-Ɏ][\w\-‐‑\']+,\s+[A-Z]\.', author_section))
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
            expected="Lastname, I., & Other",
            actual="Lastname, I. & Other",
        )
    return None


def check_period_after_year(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R009: Year parenthesis must be followed by a period — '(2020). Title'
    not '(2020) Title'."""
    if _MISSING_PERIOD_AFTER_YEAR_RE.search(para.raw_text):
        return _issue(
            "R009",
            "Missing period after year (APA 7th R009)",
            expected="(2020). Title",
            actual="(2020) Title",
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
            expected="Journal Name, 12(3)",
            actual="Journal Name 12(3)",
        )
    return None


def check_hyphen_spacing(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R011: Hyphens between words must not have adjacent spaces —
    'meta-analysis' not 'meta- analysis' or 'meta -analysis'."""
    if _HYPHEN_SPACE_RE.search(para.raw_text):
        return _issue(
            "R011",
            "Hyphen should not have adjacent space (APA 7th R011)",
            expected="meta-analysis",
            actual="meta- analysis",
        )
    return None


def check_chapter_editors(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R014: A book chapter must name its editors with (Ed.)/(Eds.)."""
    if not _is_chapter(para.raw_text) or _EDITORS_RE.search(para.raw_text):
        return None
    return _issue(
        "R014",
        "Book chapter should name the editors with (Eds.) (APA 7th R014)",
        expected="In E. E. Editor (Eds.), Book title (pp. xx–xx). Publisher.",
        actual="In Book title (pp. xx–xx). Publisher.",
    )


def check_chapter_page_format(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R015: Chapter page range must use 'pp.' — '(pp. 351–381)' not '(351–381)'."""
    if not _is_chapter(para.raw_text) or _PP_OK_RE.search(para.raw_text):
        return None
    if _BARE_PAGES_RE.search(para.raw_text):
        return _issue(
            "R015",
            "Book chapter page range should use 'pp.' (APA 7th R015)",
            expected="(pp. 351–381)",
            actual="(351–381)",
        )
    return None


def check_chapter_editor_order(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R016: Editors use 'F. M. Last' order (initials first), not 'Last, F. M.'."""
    if not _is_chapter(para.raw_text):
        return None
    m = re.search(r'\bIn\s+(.+?)\(\s*[Ee]ds?\.\s*\)', para.raw_text, re.DOTALL)
    if not m:
        return None  # no editors block — R014 covers the missing-editors case
    if _EDITOR_AUTHOR_ORDER_RE.search(m.group(1)):
        return _issue(
            "R016",
            "Editor names should be in 'F. M. Last' order, initials first (APA 7th R016)",
            expected="In E. E. Editor (Eds.),",
            actual="In Editor, E. E. (Eds.),",
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
    check_hyphen_spacing,
    check_chapter_editors,
    check_chapter_page_format,
    check_chapter_editor_order,
]
