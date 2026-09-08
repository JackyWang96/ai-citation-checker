"""Check an LLM-written reference against the APA rules.

Runs the same deterministic rules the checker applies to the user's document,
so a suggestion has to clear the bar the original failed. Costs nothing — no
API call — which is what makes a retry loop affordable.

The catch is that a suggestion is a bare string with no formatting, so rules
that judge layout can only ever report a false failure on it.
"""
from __future__ import annotations

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


def validate_suggestion(text: str) -> list[str]:
    """Return the rules the suggestion still violates; empty means it passes.

    `has_hanging_indent=True` is asserted rather than measured — the string
    has no paragraph to measure — which keeps R006 quiet even if it is
    re-enabled upstream.
    """
    para = ReferenceParagraph(raw_text=text, runs=[], has_hanging_indent=True)
    return [
        f"[{issue.rule_id}] {issue.reason}"
        for issue in validate_reference_paragraph(para)
        if issue.rule_id not in _FORMATTING_ONLY_RULES
    ]
