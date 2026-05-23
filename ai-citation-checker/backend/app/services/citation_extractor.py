from __future__ import annotations
import re
from dataclasses import dataclass

# Matches in-text citations like:
#   (Smith, 2020), (Smith & Jones, 2019, p. 15), (Smith et al., 2021),
#   (Smith, Jones, & Brown, 2020) — non-compliant but we still extract it
#   to flag the format violation.
_INTEXT_RE = re.compile(
    r'\(([A-ZÀ-ɏ][a-zA-ZÀ-ɏ\-\.\s,&\']*?)'
    r',\s*(\d{4}[a-z]?)'
    r'(?:,\s*pp?\.\s*[\d\-–]+)?\)'
)

_YEAR_RE = re.compile(r'\((\d{4}[a-z]?)\)')
_AUTHOR_RE = re.compile(
    r'^((?:[A-ZÀ-ɏ][\wÀ-ɏ\-]*\s+)*[A-ZÀ-ɏ][\wÀ-ɏ\-]+\.?)'
    r'(?:,\s+[A-Z]|\.?\s+\()'
)


@dataclass
class IntextCitation:
    raw_text: str
    author: str
    year: int
    char_start: int
    char_end: int
    # Second author surname (empty if single-author or et al.) — used to verify
    # that the in-text citation matches the *right* reference, not just any
    # reference by the same first author + year.
    second_author: str = ""
    # Total distinct authors named (excluding et al.) — used to flag the APA 7th
    # rule that 3+ authors should use "et al." instead of listing them all.
    n_authors: int = 1
    has_etal: bool = False


@dataclass
class ReferenceEntry:
    raw_text: str
    first_author_normalized: str
    year: int
    title_normalized: str
    doi: str | None = None
    # Original title with case and punctuation preserved — used for UI display
    # so users see what they actually wrote, not the lowercased match-key.
    title_raw: str = ""
    # Second author surname (empty if single-author entry) — used to verify
    # in-text citations match the right reference when first author + year alone
    # is ambiguous (e.g. same first author cited in two different papers).
    second_author_normalized: str = ""


def _parse_intext_authors(author_str: str) -> tuple[list[str], bool]:
    """Parse the author portion of an in-text citation.
    Examples:
      'Smith'                  -> (['Smith'],                   False)
      'Smith & Jones'          -> (['Smith', 'Jones'],          False)
      'Smith et al.'           -> (['Smith'],                   True)
      'Smith, Jones, & Brown'  -> (['Smith', 'Jones', 'Brown'], False)
    """
    has_etal = bool(re.search(r'\bet\s+al\.?', author_str))
    cleaned = re.sub(r'\s*,?\s*\bet\s+al\.?', '', author_str).strip()
    parts = re.split(r'\s*(?:&|,)\s*', cleaned)
    parts = [p.strip() for p in parts if p.strip()]
    return parts, has_etal


def extract_intext_citations(text: str) -> list[IntextCitation]:
    results = []
    for m in _INTEXT_RE.finditer(text):
        authors, has_etal = _parse_intext_authors(m.group(1).strip())
        year = int(m.group(2)[:4])
        first = authors[0] if authors else ""
        second = authors[1] if len(authors) >= 2 else ""
        results.append(IntextCitation(
            raw_text=m.group(0),
            author=first,
            second_author=second,
            n_authors=len(authors),
            has_etal=has_etal,
            year=year,
            char_start=m.start(),
            char_end=m.end(),
        ))
    return results


def parse_reference_entries(raw_paragraphs: list[str]) -> list[ReferenceEntry]:
    entries = []
    for raw in raw_paragraphs:
        raw = raw.strip()
        if not raw:
            continue
        year = _extract_year(raw)
        author = _extract_first_author(raw)
        second = _extract_second_author(raw)
        title = _extract_title(raw)
        doi = _extract_doi(raw)
        entries.append(ReferenceEntry(
            raw_text=raw,
            first_author_normalized=normalize_author(author),
            second_author_normalized=normalize_author(second),
            year=year,
            title_normalized=normalize_title(title),
            doi=doi,
            title_raw=title,
        ))
    return entries


def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r'[^\w\s]', '', title)
    return re.sub(r'\s+', ' ', title).strip()


def normalize_author(author: str) -> str:
    return author.lower().strip()


def _extract_year(text: str) -> int:
    m = _YEAR_RE.search(text)
    return int(m.group(1)[:4]) if m else 0


def _extract_first_author(text: str) -> str:
    m = _AUTHOR_RE.match(text)
    return m.group(1) if m else ""


def _extract_second_author(text: str) -> str:
    """Find the surname of the second author in a reference entry, if any.
    Handles 'Smith, J., & Jones, A.', 'Smith, J., Jones, A., & Brown, B.', etc.
    Returns '' for single-author entries."""
    m = re.search(
        # First author (surname, initials)
        r'^[A-ZÀ-ɏ][\w\-‐‑\']+,\s*(?:[A-ZÀ-ɏ]\.\s*)+'
        # Separator: ", " optionally followed by "& "
        r',\s*(?:&\s+)?'
        # Second surname (followed by another comma — initials come next)
        r'([A-ZÀ-ɏ][\w\-‐‑\']+)\s*,',
        text,
    )
    return m.group(1) if m else ""


def _extract_title(text: str) -> str:
    # Period after year-parens is optional — APA requires it but many users
    # omit it (e.g. "(2010) More than words"). We still extract the title.
    m = re.search(r'\(\d{4}[a-z]?\)\.?\s+(.+?)[\.\!\?]', text)
    return m.group(1).strip() if m else ""


def _extract_doi(text: str) -> str | None:
    m = re.search(r'https?://doi\.org/(\S+)', text)
    return m.group(1).rstrip('.,;') if m else None
