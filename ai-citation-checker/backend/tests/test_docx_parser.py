import pytest
from pathlib import Path
from app.services.docx_parser import parse_docx, ParsedDocument

FIXTURES = Path(__file__).parent / "fixtures"

def test_parse_returns_parsed_document():
    data = (FIXTURES / "clean.docx").read_bytes()
    result = parse_docx(data)
    assert isinstance(result, ParsedDocument)
    assert len(result.full_text) > 0
    assert len(result.reference_paragraphs) > 0

def test_body_text_excludes_references_section():
    data = (FIXTURES / "clean.docx").read_bytes()
    result = parse_docx(data)
    assert "References" not in result.body_text or result.body_text.index("References") == 0

def test_reference_paragraphs_have_italic_info():
    data = (FIXTURES / "format_errors.docx").read_bytes()
    result = parse_docx(data)
    # Each para is a list of (text, is_italic) tuples
    for para in result.reference_paragraphs:
        assert isinstance(para.runs, list)
        for run_text, is_italic in para.runs:
            assert isinstance(run_text, str)
            assert isinstance(is_italic, bool)

def test_malformed_docx_raises_value_error():
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_docx(b"not a docx file")
