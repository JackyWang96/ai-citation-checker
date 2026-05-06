from __future__ import annotations
import re
from dataclasses import dataclass

# Matches: (Smith, 2020), (Smith & Jones, 2019, p. 15), (Smith et al., 2021)
_INTEXT_RE = re.compile(
    r'\(([A-Z][a-zA-Z\-]+(?:\s+et\s+al\.)?'
    r'(?:\s*[,&]\s*[A-Z][a-zA-Z\-]+)*)'
    r',\s*(\d{4}[a-z]?)'
    r'(?:,\s*pp?\.\s*[\d\-]+)?\)'
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


@dataclass
class ReferenceEntry:
    raw_text: str
    first_author_normalized: str
    year: int
    title_normalized: str
    doi: str | None = None


def extract_intext_citations(text: str) -> list[IntextCitation]:
    results = []
    for m in _INTEXT_RE.finditer(text):
        author_part = m.group(1).split(",")[0].strip()
        author_part = re.sub(r'\s+et\s+al\.?', '', author_part).strip()
        year = int(m.group(2)[:4])
        results.append(IntextCitation(
            raw_text=m.group(0),
            author=author_part,
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
        title = _extract_title(raw)
        doi = _extract_doi(raw)
        entries.append(ReferenceEntry(
            raw_text=raw,
            first_author_normalized=normalize_author(author),
            year=year,
            title_normalized=normalize_title(title),
            doi=doi,
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


def _extract_title(text: str) -> str:
    m = re.search(r'\(\d{4}[a-z]?\)\.\s+(.+?)[\.\!\?]', text)
    return m.group(1).strip() if m else ""


def _extract_doi(text: str) -> str | None:
    m = re.search(r'https?://doi\.org/(\S+)', text)
    return m.group(1).rstrip('.,;') if m else None
