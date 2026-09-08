from __future__ import annotations
import re
from typing import Optional
from app.models.schemas import CitationIssue
from app.services.docx_parser import ReferenceParagraph

# Surname character class — uppercase Latin/extended Latin (À-Ɏ covers French,
# German, Spanish, Polish, Czech, Nordic, etc.) followed by any word char,
# hyphen, en/em dash, or apostrophe (for names like O'Brien, Pekarek-Doehler).
_AUTHOR_FORMAT_RE = re.compile(
    # Allow multi-word surnames ('Van Vu, D.', 'Pekarek Doehler, S.') — each
    # extra word must itself look like a name part. Keep in sync with the
    # extractor's _AUTHOR_RE which already accepts multi-word surnames.
    r'^[A-ZÀ-Ɏ][\w\-‐‑\']+(?:\s+[A-ZÀ-Ɏ][\w\-‐‑\']+)*,\s+[A-ZÀ-Ɏ]\.'
)
_YEAR_PARENS_RE = re.compile(r'\(\d{4}[a-z]?\)')
_DOI_URL_RE = re.compile(r'https?://doi\.org/')
# R021 — 'are page numbers present at all?', in any written form. Broader
# than _PAGE_GROUP_RE, which requires 'pp.' to directly follow the opening
# paren and so misses the common '(Vol. 2, pp. 27-44)'. Also matches a bare
# range written without parentheses (', 441-461.'), which is malformed but
# is still page information, so R021 must not claim it is absent.
_PP_ANYWHERE_RE = re.compile(r'\bpp?\.\s*\d')
_ANY_PAGES_RE = re.compile(r'\bpp?\.\s*\d|\d+\s*[-–—]\s*\d+')
# Page range with no parentheses at all: 'Handbook of X, 441-461. Publisher.'
# Anchored on the comma before and the period after so it only matches the
# slot where the page element belongs.
_BARE_PAGES_NO_PARENS_RE = re.compile(r',\s*\d+\s*[-–—]\s*\d+\s*\.')
# R022 — APA 6 publisher location: the reference's final element written as
# 'City: Publisher.' or 'City, Region: Publisher.'. Anchored to the end of
# the (locator-stripped) text so a colon inside a title or subtitle, which
# is always followed by further elements, cannot match.
_PUBLISHER_LOCATION_RE = re.compile(
    r"(?:^|\.\s)"
    r"([A-Z][\w.'\u2019\-]*(?:\s+[A-Z][\w.'\u2019\-]*)*"
    r"(?:,\s*[A-Z][\w.'\u2019\-]*(?:\s+[A-Z][\w.'\u2019\-]*)*)?)"
    r":\s+([A-Z][^.]*)\.\s*$"
)
# Whole DOI / URL spans. These must be removed before looking for page
# ranges: a Springer-style DOI suffix ('10.1007/978-3-319-12345-6_7')
# contains digit-hyphen-digit runs that read as a page range and would
# silence R021 on exactly the chapters that carry such DOIs.
_LOCATOR_SPAN_RE = re.compile(
    r'https?://\S+|\bdoi:\s*\S+|\b10\.\d{4,9}/\S+', re.IGNORECASE
)
# Any locator that lets a reader reach the entry without page numbers:
# a DOI in any written form, or a plain URL.
_LOCATOR_RE = re.compile(r'https?://|\bdoi:\s*10\.|\b10\.\d{4,9}/', re.IGNORECASE)
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
# Second alternation covers journals without issue numbers: "Name 45, 373-401"
# (volume followed directly by a page range) also needs a comma after the
# journal name. Dates like "May 28, 2026, from" don't match — no page range.
_MISSING_COMMA_BEFORE_VOLUME_RE = re.compile(
    r'[A-Za-z]\s+\d+\(\d+\)'
    r'|[A-Za-z]\s+\d+\s*,\s*\d+\s*[-–—]\s*\d+'
)

# R011 — hyphen with adjacent space in compound words, e.g. "meta- analysis"
# or "meta -analysis". Restricted to letter-hyphen-space-letter (or mirrored)
# so page ranges like "1- 10" or "pp. 1 - 10" don't false-trigger.
# The surrounding word characters are captured so the issue can show the
# user's actual offending snippet, not a canned example.
_HYPHEN_SPACE_RE = re.compile(
    r"[\w'‐‑-]*[A-Za-zÀ-ɏ](?:-\s+|\s+-)[A-Za-zÀ-ɏ][\w'‐‑-]*"
)

# --- Book-chapter detection (R014–R016) ---------------------------------
# An edited-book chapter looks like:
#   Author, A. (Year). Chapter title. In E. E. Editor (Eds.), Book title
#   (pp. 123–145). Publisher.
# We detect the ". In <Capital>" source marker, then require either a page
# group or an editors group so journal titles containing "In" don't trigger.
_CHAPTER_IN_RE = re.compile(r'\.\s+In\s+[A-Z0-9À-Ɏ]')
# Lenient — period is optional. Used for "are editors named at all?" detection
# in _is_chapter and R014. R017 separately enforces the period.
_EDITORS_RE = re.compile(r'\(\s*[Ee]ds?\.?\s*\)')
# Strict — period required. Used by R017 to flag '(Eds)' / '(Ed)' as the
# missing-period typo (APA 7 requires '(Eds.)' / '(Ed.)').
_EDITORS_STRICT_RE = re.compile(r'\(\s*[Ee]ds?\.\s*\)')
_PAGE_GROUP_RE = re.compile(r'\(\s*pp?\.\s*\d|\(\s*\d+\s*[-–—]\s*\d+\s*\)')
# Correct chapter page format: "(pp. 351" or "(p. 5".
_PP_OK_RE = re.compile(r'\(\s*pp?\.\s*\d')
# Bare page range with no pp./p. prefix: "(351–381)".
_BARE_PAGES_RE = re.compile(r'\(\s*\d+\s*[-–—]\s*\d+\s*\)')
# Editor section written in author order ("Surname, I.") instead of "I. Surname".
_EDITOR_AUTHOR_ORDER_RE = re.compile(r'[A-ZÀ-Ɏ][\w\-‐‑\']+,\s+[A-Z]\.')


def _without_locators(text: str) -> str:
    """Blank out DOIs and URLs so their digits can't be read as pages."""
    return _LOCATOR_SPAN_RE.sub(' ', text)


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
    ('. ', '? ' or '! ' — titles can end with a question/exclamation mark,
    e.g. 'To what extent do ... make use of collocations?') and the
    volume/issue marker.

    NBSP (U+00A0) is normalised to a regular space first: Word often inserts
    non-breaking spaces between sentences ("proficiency.\\xa0Behavior") and
    the literal terminator lookup below would miss them, swallowing the
    article title into the extracted journal name."""
    text = text.replace("\xa0", " ")
    m = _JOURNAL_STRUCT_RE.search(text)
    if not m:
        return None
    before = text[:m.start()].rstrip().rstrip(',').rstrip()
    idx = max(before.rfind('. '), before.rfind('? '), before.rfind('! '))
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
    'meta-analysis' not 'meta- analysis' or 'meta -analysis'.

    expected/actual carry the user's actual offending snippet (previously a
    hard-coded 'meta- analysis' example, which confused users whose text
    contained no such word)."""
    m = _HYPHEN_SPACE_RE.search(para.raw_text)
    if m:
        snippet = m.group()
        return _issue(
            "R011",
            "Hyphen should not have adjacent space (APA 7th R011)",
            expected=re.sub(r'\s*-\s*', '-', snippet),
            actual=snippet,
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
    """R015: chapter page range must be written '(pp. 351-381)'.

    Two malformed shapes are caught: parenthesised without the 'pp.' prefix
    ('(351-381)'), and no parentheses at all (', 441-461.'). The second was
    found in References check03, where three references written in APA 6 style
    put the range after a comma; the old parentheses-only pattern let all
    three pass with no issue at all.

    The 'already correct' test looks for 'pp.' anywhere rather than directly
    after an opening paren, so the common '(Vol. 2, pp. 27-44)' counts as
    correct.
    """
    text = _without_locators(para.raw_text)
    if not _is_chapter(para.raw_text) or _PP_ANYWHERE_RE.search(text):
        return None
    if _BARE_PAGES_RE.search(text):
        return _issue(
            "R015",
            "Book chapter page range should use 'pp.' (APA 7th R015)",
            expected="(pp. 351-381)",
            actual="(351-381)",
        )
    if _BARE_PAGES_NO_PARENS_RE.search(text):
        return _issue(
            "R015",
            "Book chapter page range should be in parentheses with 'pp.' "
            "(APA 7th R015)",
            detail="APA 7 puts the chapter page range in parentheses after the "
                   "book title, prefixed with 'pp.'. An edition number shares "
                   "the same parentheses: '(2nd ed., pp. 109-112)'.",
            expected="(pp. 441-461).",
            actual=", 441-461.",
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


def check_chapter_editors_period(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R017: APA 7 requires a period inside the editors marker — '(Eds.)' /
    '(Ed.)', not '(Eds)' / '(Ed)'. Fires only when an editors marker is
    present but missing its period; if there's no marker at all, R014 covers
    that case with a clearer 'editors missing' message."""
    if not _is_chapter(para.raw_text):
        return None
    if not _EDITORS_RE.search(para.raw_text):
        return None  # no editors marker — R014 handles it
    if _EDITORS_STRICT_RE.search(para.raw_text):
        return None  # properly punctuated
    return _issue(
        "R017",
        "Editors marker should include a period: '(Eds.)' / '(Ed.)' (APA 7th R017)",
        expected="(Eds.)",
        actual="(Eds)",
    )


def check_chapter_page_range(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R021: a chapter / reference-work entry cited with no page range at all.

    APA 7 §10.3 gives edited-book chapters and reference-work entries a single
    template, and that template includes ``(pp. xx-xx)``. APA's own
    encyclopedia and dictionary examples omit pages only because those works
    are genuinely unpaginated.

    Deliberately structural rather than data-driven. The existing R019 asks
    Crossref for the page range and stays silent when there isn't one — but
    Crossref records no `page` field for most encyclopedia entries, so R019 is
    silent on exactly the references most likely to be missing pages. Verified
    against the reported case: Crossref returns no page for
    10.1002/9781444316568.wiem02057.

    Bug this was written for: 'Grimm, P. (2010). Social desirability bias. In
    J. Sheth & N. Malhotra (Eds.), Wiley international encyclopedia of
    marketing. Wiley. https://doi.org/...' passed as "APA format correct"
    despite having no page range.

    A DOI or URL does not excuse a missing page range — a paginated work can
    have both — but it does change the advice, so the detail text branches on
    it. Known cost: an online-only work that truly has no pagination (the
    Stanford Encyclopedia of Philosophy) is flagged too. The wording says so
    rather than asserting the reference is wrong.
    """
    if not _is_chapter(para.raw_text):
        return None
    if _ANY_PAGES_RE.search(_without_locators(para.raw_text)):
        return None   # pages present in some form; R015 covers bad formatting

    if _LOCATOR_RE.search(para.raw_text):
        detail = ("APA 7 chapter and reference-work entries take (pp. xx-xx) "
                  "after the book title. Add the page range from the published "
                  "work. If this entry is from an online-only reference work "
                  "with no pagination, it has no page range and the reference "
                  "is already correct.")
    else:
        detail = ("APA 7 chapter and reference-work entries take (pp. xx-xx) "
                  "after the book title. This reference has neither a page "
                  "range nor a DOI/URL, so it is incomplete either way: add "
                  "the page range, or the locator if the work is unpaginated.")
    return _issue(
        "R021",
        "Book chapter should include a page range (APA 7th R021)",
        detail=detail,
        expected="In E. E. Editor (Eds.), Book title (pp. xx-xx). Publisher.",
    )


def check_publisher_location(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R022: APA 7 dropped the publisher's location.

    'Berlin, Germany: Walter de Gruyter.' is APA 6; APA 7 wants
    'Walter de Gruyter.' alone. Worth flagging on its own because it rarely
    appears in isolation — a reference list written to the old edition tends
    to carry the location on every book and chapter, so one hit usually means
    the whole list needs revisiting.

    Anchored to the last element of the reference. A colon inside a title or
    subtitle is always followed by further elements, so it cannot match.
    Evaluated against 96 real references: 5 hits, all genuine, no false
    positives. Residual risk: a reference that ends with a title-case subtitle
    and names no publisher at all could match — but such a reference is
    incomplete regardless.
    """
    m = _PUBLISHER_LOCATION_RE.search(
        re.sub(r'\s+', ' ', _without_locators(para.raw_text)).strip()
    )
    if not m:
        return None
    location, publisher = m.group(1), m.group(2).strip()
    return _issue(
        "R022",
        "Publisher location should be removed (APA 7th R022)",
        detail="APA 7 no longer includes the publisher's city or country. "
               "Give the publisher name on its own.",
        expected=f"{publisher}.",
        actual=f"{location}: {publisher}.",
    )


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
    check_chapter_editors_period,
    check_chapter_page_range,
    check_publisher_location,
]
