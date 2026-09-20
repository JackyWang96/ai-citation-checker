from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel


class CitationIssue(BaseModel):
    type: Literal["not_found", "field_mismatch", "format_violation", "orphan", "ambiguous"]
    severity: Literal["red", "yellow"]
    category: Literal["content", "format", "orphan", "ambiguous"]
    reason: str
    detail: Optional[str] = None
    field: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    rule_id: Optional[str] = None


class TextRun(BaseModel):
    """A slice of citation text with its italic flag — sent so the UI can
    re-render italics that exist in the source document (journal/book titles
    etc.). Only populated for reference citations."""
    text: str
    italic: bool


class RuleBasis(BaseModel):
    chunk_id: str
    title: str
    source_url: str


class Citation(BaseModel):
    id: str
    kind: Literal["intext", "reference"]
    raw_text: str
    char_start: int
    char_end: int
    status: Literal["pass", "warning", "error"]
    issues: list[CitationIssue]
    verified_reference_id: Optional[str] = None
    runs: Optional[list[TextRun]] = None
    # Stage 2 — populated only after the user runs LLM analysis on the report.
    suggestion: Optional[str] = None
    suggestion_explanation: Optional[str] = None
    # False means the rewrite never passed the checker's own rules. Such a
    # suggestion is still shown — it is usually closer than the original — but
    # it must be presented as an unverified draft, never as a verified fix.
    suggestion_verified: bool = False
    # The APA guidance the rewrite relied on. Title and URL are read from the
    # retrieved chunks, never from the model's output — a hallucinated source
    # link would be a fabricated citation used to justify a fix, in a tool
    # built to catch fabricated citations.
    suggestion_rule_basis: list[RuleBasis] = []
    # Rules the suggestion still violates; only populated when unverified.
    suggestion_validation: list[str] = []
    # "declined" means the loop ran and produced nothing usable. Without it the
    # citation looks unanalysed forever, so the button stays lit and every
    # click pays for the same work again.
    suggestion_status: Optional[Literal["declined"]] = None


class Report(BaseModel):
    id: str
    filename: str
    created_at: datetime
    expires_at: datetime
    full_text: str
    citations: list[Citation]
    summary: dict[str, int]
