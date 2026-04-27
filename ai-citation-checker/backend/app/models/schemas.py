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


class Citation(BaseModel):
    id: str
    kind: Literal["intext", "reference"]
    raw_text: str
    char_start: int
    char_end: int
    status: Literal["pass", "warning", "error"]
    issues: list[CitationIssue]
    verified_reference_id: Optional[str] = None


class Report(BaseModel):
    id: str
    filename: str
    created_at: datetime
    expires_at: datetime
    full_text: str
    citations: list[Citation]
    summary: dict
