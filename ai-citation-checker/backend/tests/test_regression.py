"""
Regression tests — one test per bug found in production.
Each test documents WHAT broke, WHY, and verifies the fix.
"""
import pytest
import respx
import httpx
from app.services.citation_extractor import parse_reference_entries
from app.services.docx_parser import ReferenceParagraph
from app.rules.apa7 import check_author_separator
from app.services.report_builder import _compare_fields, build_report
from app.services.verifier import VerifyResult, _search_openalex
from app.services.citation_extractor import ReferenceEntry


# ── citation_extractor ────────────────────────────────────────────────────────

def test_unicode_author_name_lindstrom():
    """Bug: _AUTHOR_RE only matched ASCII, so 'Lindström' extracted empty author."""
    entries = parse_reference_entries([
        "Lindström, A. (2005). Language as social action. Journal, 1(1), 1–10."
    ])
    assert entries[0].first_author_normalized == "lindström"


def test_multiword_author_pekarek_doehler():
    """Bug: regex stopped at first word, extracting 'Pekarek' instead of 'Pekarek Doehler'."""
    entries = parse_reference_entries([
        "Pekarek Doehler, S. (2019). On the nature of L2 competence. Journal, 1(1), 1–10."
    ])
    assert entries[0].first_author_normalized == "pekarek doehler"


def test_organisation_name_no_initials():
    """Bug: org names like 'IELTS Partners (2023)' failed author extraction."""
    entries = parse_reference_entries([
        "IELTS Partners (2023). IELTS and the CEFR. Retrieved from https://ielts.org/"
    ])
    assert entries[0].first_author_normalized == "ielts partners"


def test_doi_extracted_without_url_prefix():
    """Bug: _extract_doi returned full URL 'https://doi.org/10.x', causing
    DOI lookup URL to become '...works/https://doi.org/10.x' (invalid)."""
    entries = parse_reference_entries([
        "Walther, J. B. (1992). Title. Journal, 19, 52–90. https://doi.org/10.1177/009365092019001003"
    ])
    assert entries[0].doi == "10.1177/009365092019001003"
    assert not entries[0].doi.startswith("http")


# ── docx_parser ───────────────────────────────────────────────────────────────

def test_hanging_indent_detected_via_style(tmp_path):
    """Bug: has_hanging_indent only checked direct paragraph format, not style inheritance.
    Most Word docs define hanging indent via 'Bibliography' style → always False → R006 false positive."""
    from docx import Document
    from docx.shared import Pt
    doc = Document()
    style = doc.styles["Normal"]
    # Create a style with hanging indent
    ref_style = doc.styles.add_style("BibTest", 1)
    ref_style.paragraph_format.first_line_indent = -457200  # -0.5 inch via style

    para = doc.add_paragraph("Smith, J. (2020). Title. Journal, 1(1).")
    para.style = ref_style
    # Direct indent is NOT set — only the style has it

    import io
    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)

    from app.services.docx_parser import parse_docx, REFERENCE_HEADINGS
    # Add a References heading first
    doc2 = Document()
    ref_style2 = doc2.styles.add_style("BibTest2", 1)
    ref_style2.paragraph_format.first_line_indent = -457200
    doc2.add_heading("References", level=1)
    p = doc2.add_paragraph("Smith, J. (2020). Title. Journal, 1(1).")
    p.style = ref_style2
    buf2 = io.BytesIO()
    doc2.save(buf2)
    buf2.seek(0)
    result = parse_docx(buf2.read())
    assert result.reference_paragraphs[0].has_hanging_indent is True


def test_multi_paragraph_reference_merged():
    """Bug: a single reference split across two paragraphs by a hard line break
    was being parsed as two separate references (one without author, one without title)."""
    import io
    from docx import Document as DocxDocument
    from app.services.docx_parser import parse_docx

    doc = DocxDocument()
    doc.add_heading("References", level=1)
    doc.add_paragraph("Alzahrani, A. (2024). LexArabic: A receptive vocabulary size test to estimate Arabic")
    doc.add_paragraph("proficiency. Behavior Research Methods, 56(6), 5529–5556. https://doi.org/10.3758/s13428-023-02286-z")
    doc.add_paragraph("Arnon, I., & Christiansen, M. H. (2017). The role of multiword building blocks.")

    buf = io.BytesIO()
    doc.save(buf)
    parsed = parse_docx(buf.getvalue())

    assert len(parsed.reference_paragraphs) == 2
    first = parsed.reference_paragraphs[0].raw_text
    assert "Alzahrani" in first
    assert "doi.org" in first  # continuation line was merged in


def test_appendix_paragraphs_excluded_from_references(tmp_path):
    """Bug: parser kept collecting paragraphs after 'Appendix A' heading,
    treating appendix content as reference entries."""
    import io
    from docx import Document as DocxDocument
    from app.services.docx_parser import parse_docx

    doc = DocxDocument()
    doc.add_heading("References", level=1)
    doc.add_paragraph("Smith, J. (2020). Real ref. Journal, 1(1), 1–10.")
    doc.add_heading("Appendix A", level=1)
    doc.add_paragraph("This is appendix content, not a reference.")

    buf = io.BytesIO()
    doc.save(buf)
    result = parse_docx(buf.getvalue())

    raw_texts = [p.raw_text for p in result.reference_paragraphs]
    assert any("Real ref" in t for t in raw_texts)
    assert not any("appendix content" in t for t in raw_texts)


# ── apa7 rules ────────────────────────────────────────────────────────────────

def test_r007_and_in_title_not_flagged():
    """Bug: R007 searched full paragraph text for 'and', matching words in the
    title like 'Proficiency and sequential organization' — false positive."""
    raw = (
        "Al-Gahtani, S., & Roever, C. (2012). "
        "Proficiency and sequential organization of L2 requests. "
        "Applied Linguistics, 33(1), 42–65."
    )
    para = ReferenceParagraph(raw_text=raw, runs=[], has_hanging_indent=True)
    assert check_author_separator(para) is None


def test_r007_and_in_author_section_flagged():
    """R007 should still fire when 'and' is used in the actual author list."""
    raw = "Smith, J. and Jones, A. (2020). Title. Journal, 1(1)."
    para = ReferenceParagraph(raw_text=raw, runs=[], has_hanging_indent=True)
    issue = check_author_separator(para)
    assert issue is not None
    assert issue.rule_id == "R007"


# ── report_builder ────────────────────────────────────────────────────────────

def test_unicode_hyphen_author_not_flagged_as_mismatch():
    """Bug: Crossref stores 'Al‐Gahtani' with Unicode hyphen U+2010, document
    has 'Al-Gahtani' with ASCII hyphen — compared as unequal → false warning."""
    entry = ReferenceEntry(
        raw_text="Al-Gahtani, S. (2022). Title. Journal, 55(2), 610–634.",
        first_author_normalized="al-gahtani",
        year=2022,
        title_normalized="title",
        doi="10.1111/flan.12603",
    )
    canonical = {
        "author": [{"family": "Al‐Gahtani", "given": "S"}],  # U+2010
        "published": {"date-parts": [[2022]]},
        "title": ["Title"],
        "container-title": ["Foreign Language Annals"],
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    author_issues = [i for i in issues if i.field == "author"]
    assert len(author_issues) == 0


def test_online_first_year_not_flagged_as_mismatch():
    """Bug: Crossref 'published' field is online-first date (2011), document
    correctly cites print year (2012) → false year mismatch warning."""
    entry = ReferenceEntry(
        raw_text="Al-Gahtani, S., & Roever, C. (2012). Title. Applied Linguistics, 33(1), 42–65.",
        first_author_normalized="al-gahtani",
        year=2012,
        title_normalized="title",
        doi="10.1093/applin/amr031",
    )
    canonical = {
        "author": [{"family": "Al-Gahtani", "given": "S"}],
        "published": {"date-parts": [[2011, 9, 24]]},       # online-first
        "published-print": {"date-parts": [[2012, 2]]},     # print year
        "title": ["Title"],
        "container-title": ["Applied Linguistics"],
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    year_issues = [i for i in issues if i.field == "year"]
    assert len(year_issues) == 0


def test_not_found_includes_detailed_reason():
    """Bug: 'Reference not found' had no detail — impossible to tell if failure
    was a timeout, rate limit, or genuinely missing paper."""
    entry = ReferenceEntry(
        raw_text="Ghost, A. (2099). Nonexistent. Fake Journal.",
        first_author_normalized="ghost",
        year=2099,
        title_normalized="nonexistent",
    )
    para = ReferenceParagraph(raw_text=entry.raw_text, runs=[(entry.raw_text, True)], has_hanging_indent=True)
    vr = VerifyResult(found=False, not_found_reason="Crossref: timeout · OpenAlex: no results")

    report = build_report(
        report_id="t1", filename="test.docx", full_text=entry.raw_text,
        intext_citations=[],
        reference_entries=[entry],
        reference_paragraphs=[para],
        verify_results=[vr],
    )
    not_found_issue = next(
        i for c in report.citations for i in c.issues if i.type == "not_found"
    )
    assert not_found_issue.detail is not None
    assert "timeout" in not_found_issue.detail


# ── verifier ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_web_reference_marked_yellow_not_red(tmp_path):
    """Web/organisation citations ('Retrieved from https://...') aren't in
    Crossref/OpenAlex. Don't flag them as red 'not found' — they need manual
    verification, which is a yellow warning, not an error."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import verify_reference
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="IELTS Partners (2023). IELTS and the CEFR. Retrieved 22 November 2023 from https://ielts.org/foo",
        first_author_normalized="ielts partners",
        year=2023,
        title_normalized="ielts and the cefr",
    )
    result = await verify_reference(entry, db_path)
    assert result.found is False
    assert "web" in (result.not_found_reason or "")

    para = ReferenceParagraph(raw_text=entry.raw_text, runs=[(entry.raw_text, True)], has_hanging_indent=True)
    report = build_report(
        report_id="t", filename="t.docx", full_text=entry.raw_text,
        intext_citations=[],
        reference_entries=[entry],
        reference_paragraphs=[para],
        verify_results=[result],
    )
    citation = report.citations[0]
    # Yellow warning, not red error
    assert citation.status == "warning"
    not_found = next(i for i in citation.issues if i.type == "not_found")
    assert not_found.severity == "yellow"


@pytest.mark.asyncio
@respx.mock
async def test_republished_chapter_accepted_with_year_warning(tmp_path):
    """Bug: Crossref entries for republished chapters often carry the reprint
    year, not the original publication year (e.g. document cites Schegloff 2006,
    Crossref has the same chapter dated 2020). Title and author still match
    exactly, so reject would be wrong. Accept when title is a perfect match."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Schegloff, E. A. (2006). Interaction: The infrastructure for social institutions.",
        first_author_normalized="schegloff",
        year=2006,
        title_normalized="interaction the infrastructure for social institutions",
    )
    reprint_hit = {
        "status": "ok",
        "message": {
            "items": [{
                "title": ["Interaction: The Infrastructure for Social Institutions"],
                "author": [{"family": "Schegloff"}],
                "published": {"date-parts": [[2020]]},   # reprint year, not 2006
                "DOI": "10.1/reprint",
            }]
        }
    }
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=reprint_hit)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None
    assert result.found is True


@pytest.mark.asyncio
@respx.mock
async def test_multiword_surname_matches_truncated_crossref(tmp_path):
    """Bug: Crossref sometimes truncates multi-word surnames in book chapters.
    Reference says 'Pekarek Doehler, S.' but Crossref returns family='Doehler'.
    Strict equality failed → false 'not found'. Accept last-word match."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Pekarek Doehler, S. (2019). On the Nature of L2.",
        first_author_normalized="pekarek doehler",
        year=2019,
        title_normalized="on the nature of l2 interactional competence",
    )
    truncated_surname_hit = {
        "status": "ok",
        "message": {
            "items": [{
                "title": ["On the Nature of L2 Interactional Competence"],
                "author": [{"family": "Doehler"}],   # truncated!
                "published": {"date-parts": [[2019]]},
                "DOI": "10.1/abc",
            }]
        }
    }
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=truncated_surname_hit)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None
    assert result.found is True


@pytest.mark.asyncio
@respx.mock
async def test_missing_metadata_not_treated_as_mismatch(tmp_path):
    """Bug: Crossref returns book chapters with PARTIAL metadata (e.g. authors
    populated but year=0, OR title+year populated but authors=[]). Our verifier
    rejected both as 'author/year mismatch', leaving the user with no match
    even though the title was a perfect 100 score. Fix: treat missing fields
    as 'unknown' rather than 'mismatch'."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Drew, P., & Walker, T. (2010). Citizens' emergency calls. Routledge.",
        first_author_normalized="drew",
        year=2010,
        title_normalized="citizens emergency calls requesting assistance",
    )

    # Both entries are the same chapter — Crossref split metadata across two records.
    partial_results = {
        "status": "ok",
        "message": {
            "items": [
                {   # has year but no authors
                    "title": ["Citizens' emergency calls Requesting assistance"],
                    "author": [],
                    "published": {"date-parts": [[2010]]},
                    "DOI": "10.4324/9780203855607-18",
                },
                {   # has authors but year missing
                    "title": ["Citizens' emergency calls"],
                    "author": [{"family": "Drew"}, {"family": "Walker"}],
                    "published": {"date-parts": [[0]]},
                    "DOI": "10.4324/9780203855607.ch7",
                },
            ]
        }
    }
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=partial_results)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None
    assert result.found is True


@pytest.mark.asyncio
@respx.mock
async def test_edited_book_matched_via_editor_field(tmp_path):
    """Bug: Crossref returns edited books with an empty `author` array — the
    editors live in the `editor` field. Our verifier ignored `editor`, so the
    author_match check failed and the (correctly matching) book was rejected
    with 'score too low or author/year mismatch'."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Drew, P., & Couper-Kuhlen, E. (Eds.) (2014). Requesting in Social Interaction.",
        first_author_normalized="drew",
        year=2014,
        title_normalized="requesting in social interaction",
    )

    edited_book_hit = {
        "status": "ok",
        "message": {
            "items": [{
                "title": ["Requesting in Social Interaction"],
                "author": [],   # empty — this is the trap
                "editor": [{"family": "Drew", "given": "P"}, {"family": "Couper-Kuhlen", "given": "E"}],
                "published": {"date-parts": [[2014]]},
                "container-title": ["John Benjamins"],
                "DOI": "10.1075/slsi.26",
                "type": "edited-book",
            }]
        }
    }

    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=edited_book_hit)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None
    assert result.found is True
    assert result.exact_match is True


@pytest.mark.asyncio
@respx.mock
async def test_openalex_empty_display_name_no_crash(tmp_path):
    """Bug: OpenAlex occasionally returns authorships with empty display_name.
    .split()[-1] on empty string → IndexError → 500 crash on upload."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Smith, J. (2020). Title. Journal, 1(1).",
        first_author_normalized="smith",
        year=2020,
        title_normalized="title",
    )

    openalex_with_empty_author = {
        "results": [{
            "title": "Title",
            "authorships": [{"author": {"display_name": ""}}],  # empty name
            "publication_year": 2020,
            "doi": None,
            "host_venue": {"display_name": "Journal"},
        }]
    }

    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=openalex_with_empty_author)
    )

    # Should not raise IndexError
    result, _ = await _search_openalex(
        httpx.AsyncClient(), entry, db_path
    )
    # Result may or may not match — the point is no crash
