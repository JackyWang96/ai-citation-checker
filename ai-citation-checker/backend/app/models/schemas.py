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
    # chunk_ids of the APA guidance the rewrite relied on. Filtered against
    # what was actually retrieved, so a hallucinated id can never reach the UI.
    suggestion_rule_basis: list[str] = []
    # Rules the suggestion still violates; only populated when unverified.
    suggestion_validation: list[str] = []


class Report(BaseModel):
    id: str
    filename: str
    created_at: datetime
    expires_at: datetime
    full_text: str
    citations: list[Citation]
    summary: dict[str, int]
