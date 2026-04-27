import pytest
from app.services.citation_extractor import (
    extract_intext_citations,
    parse_reference_entries,
    normalize_title,
    normalize_author,
    ReferenceEntry,
    IntextCitation,
)

def test_extract_single_intext():
    text = "Some claim (Smith, 2020) in this sentence."
    citations = extract_intext_citations(text)
    assert len(citations) == 1
    assert citations[0].raw_text == "(Smith, 2020)"
    assert citations[0].author == "Smith"
    assert citations[0].year == 2020

def test_extract_multiple_intext():
    text = "First (Smith, 2020) and second (Jones & Lee, 2019, p. 15)."
    citations = extract_intext_citations(text)
    assert len(citations) == 2
    assert citations[1].year == 2019

def test_extract_et_al():
    text = "See (Smith et al., 2021)."
    citations = extract_intext_citations(text)
    assert len(citations) == 1
    assert "et al" in citations[0].raw_text

def test_intext_char_positions():
    text = "Claim (Smith, 2020) here."
    citations = extract_intext_citations(text)
    start = citations[0].char_start
    end = citations[0].char_end
    assert text[start:end] == "(Smith, 2020)"

def test_parse_reference_entry():
    raw = "Smith, J. (2020). AI in education. Journal of Ed, 1(1), 1-10."
    entries = parse_reference_entries([raw])
    assert len(entries) == 1
    e = entries[0]
    assert e.first_author_normalized == "smith"
    assert e.year == 2020
    assert "ai in education" in e.title_normalized

def test_normalize_title_strips_punctuation():
    assert normalize_title("AI: A Review.") == "ai a review"

def test_normalize_author_lowercases():
    assert normalize_author("Smith") == "smith"
