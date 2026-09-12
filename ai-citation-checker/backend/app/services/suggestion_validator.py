"""Check an LLM-written reference against what the checker knows.

Two independent gates, both of which must pass before a suggestion may be
called verified:

1. the APA format rules — the same ones applied to the user's document, and
2. the field values the authoritative record supplied.

Gate 2 exists because gate 1 cannot see them. A rewrite that keeps the wrong
year but tidies the punctuation passes every format rule, and without this it
was reported as verified — the exact failure the loop is supposed to prevent.

Costs nothing: no API call, which is what makes a retry loop affordable.
"""
from __future__ import annotations

import re

from app.services.apa_validator import validate_reference_paragraph
from app.services.docx_parser import ReferenceParagraph

# Rules that need formatting information a plain string cannot carry.
#
# R003 (italics) is the live case: a correct reference validates as "Journal
# name should be in italics" purely because a str has no runs. Verified
# against known-good references — it was the only false failure.
#
# R006 (hanging indent) is not currently in ALL_RULES, so it never runs; it is
# listed because apa7.py invites re-adding it, and that would silently start
# failing every suggestion.
_FORMATTING_ONLY_RULES = frozenset({"R003", "R006"})


def _norm(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace.

    Comparison has to survive the rewrite doing its job. Crossref stores
    titles in title case while APA wants sentence case, so a correct rewrite
    changes capitalisation; DOIs and page ranges pick up or lose punctuation.
    Comparing raw strings would fail those valid rewrites.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


# Issues whose `expected` is advisory rather than authoritative. The book
# edition case says so in as many words — "verify whether your cited year
# matches the edition you used" — so demanding the rewrite adopt that year
# would push a correct citation onto another edition's year and then call the
# result verified.
_ADVISORY_REASONS = frozenset({"Possible alternative publication year"})

# The date element: "(2019)" or "(2019a)".
_YEAR_RE = re.compile(r"\((\d{4})[a-z]?\)")


def _contains_tokens(haystack: str, needle: str) -> bool:
    """Whole-token containment, not substring.

    Substring matching let short expected values be satisfied by unrelated
    words: an expected author surname of "Li" was found inside "Publishing",
    so a rewrite that kept the wrong author validated clean.
    """
    h, n = _norm(haystack).split(), _norm(needle).split()
    if not n:
        return True
    return any(h[i:i + len(n)] == n for i in range(len(h) - len(n) + 1))


def _field_failures(citation: dict, text: str) -> list[str]:
    """Field values the record supplied that the rewrite still doesn't carry.

    Each field is checked where it actually belongs. Searching the whole
    string let an expected value be satisfied from the wrong element — an
    expected year of 2019 was accepted because the *title* contained "2019
    annual review" while the date stayed 2018.
    """
    year_match = _YEAR_RE.search(text)
    authors = text[: year_match.start()] if year_match else text
    after_date = text[year_match.end():] if year_match else text

    failures = []
    for issue in citation.get("issues") or []:
        if issue.get("type") != "field_mismatch":
            continue
        if issue.get("reason") in _ADVISORY_REASONS:
            continue
        expected = (issue.get("expected") or "").strip()
        if not expected:
            continue

        field = issue.get("field") or "field"
        if field == "year":
            ok = bool(year_match) and year_match.group(1) == expected
        elif field == "author":
            ok = _contains_tokens(authors, expected)
        else:
            # title / journal / anything else: never satisfied from the author
            # element, which is where a surname would otherwise match.
            ok = _contains_tokens(after_date, expected)

        if not ok:
            failures.append(
                f"[{field}] the rewrite still does not carry the verified "
                f"{field}: expected {expected!r}"
            )
    return failures


def validate_suggestion(citation: dict, text: str) -> list[str]:
    """Return every reason the suggestion still fails; empty means it passes.

    `has_hanging_indent=True` is asserted rather than measured — the string
    has no paragraph to measure — which keeps R006 quiet even if it is
    re-enabled upstream.
    """
    para = ReferenceParagraph(raw_text=text, runs=[], has_hanging_indent=True)
    format_failures = [
        f"[{issue.rule_id}] {issue.reason}"
        for issue in validate_reference_paragraph(para)
        if issue.rule_id not in _FORMATTING_ONLY_RULES
    ]
    return format_failures + _field_failures(citation, text)
