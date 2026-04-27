from __future__ import annotations
import io
from dataclasses import dataclass, field
from docx import Document

REFERENCE_HEADINGS = {"references", "bibliography", "works cited", "reference list"}


@dataclass
class ReferenceParagraph:
    raw_text: str
    runs: list[tuple[str, bool]]      # (text, is_italic)
    has_hanging_indent: bool


@dataclass
class ParsedDocument:
    full_text: str
    body_text: str
    reference_paragraphs: list[ReferenceParagraph] = field(default_factory=list)


def parse_docx(data: bytes) -> ParsedDocument:
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Failed to parse .docx: {exc}") from exc

    paragraphs = doc.paragraphs
    ref_start = _find_references_heading(paragraphs)

    body_paras = paragraphs[:ref_start] if ref_start is not None else paragraphs
    body_text = "\n".join(p.text for p in body_paras if p.text.strip())

    ref_paras: list[ReferenceParagraph] = []
    if ref_start is not None:
        for para in paragraphs[ref_start + 1:]:
            if not para.text.strip():
                continue
            runs = [(r.text, bool(r.italic)) for r in para.runs if r.text]
            fmt = para.paragraph_format
            hanging = (
                fmt.first_line_indent is not None and fmt.first_line_indent < 0
            )
            ref_paras.append(ReferenceParagraph(
                raw_text=para.text,
                runs=runs,
                has_hanging_indent=hanging,
            ))

    full_text = "\n".join(p.text for p in paragraphs if p.text.strip())
    return ParsedDocument(
        full_text=full_text,
        body_text=body_text,
        reference_paragraphs=ref_paras,
    )


def _find_references_heading(paragraphs) -> int | None:
    for i, para in enumerate(paragraphs):
        if para.style.name.startswith("Heading") and para.text.strip().lower() in REFERENCE_HEADINGS:
            return i
        if para.text.strip().lower() in REFERENCE_HEADINGS:
            return i
    return None
