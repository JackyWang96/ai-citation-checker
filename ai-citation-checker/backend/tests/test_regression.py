"""
Regression tests — one test per bug found in production.
Each test documents WHAT broke, WHY, and verifies the fix.
"""
import json
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


def test_title_extracted_when_period_after_year_missing():
    """Bug: _extract_title required a period after '(YYYY)'. Users who wrote
    '(2010) Title' (forgetting the period — common APA mistake) had their
    title parsed as empty, causing 'actual: (empty)' in the UI title mismatch
    warning."""
    from app.services.citation_extractor import _extract_title
    raw = "Arnon, I. & Snider, N. (2010) More than words. Journal, 62(1), 67–82."
    assert _extract_title(raw) == "More than words"


def test_r001_unicode_author_names_not_flagged():
    """Bug: R001 author-format regex was ASCII-only ([A-Z][a-zA-Z]+), so any
    surname or initial with a non-ASCII letter (Ferré, Lindström, Çelik, Ö.)
    triggered a false positive 'Author format should be Last, F. M.' warning.
    Fix: include extended Latin range [À-Ɏ] in both surname and initial."""
    from app.rules.apa7 import check_author_format

    correctly_formatted = [
        "Ferré, P., & Brysbaert, M. (2017). Test.",         # French é
        "Lindström, A. (2005). Test.",                       # Swedish ö
        "Çelik, Ö. (2020). Test.",                           # Turkish Ç and Ö (initial)
        "Łukasiewicz, J. (1999). Test.",                     # Polish Ł
        "O'Brien, M. (2018). Test.",                         # Apostrophe in surname
    ]
    for raw in correctly_formatted:
        para = ReferenceParagraph(raw_text=raw, runs=[], has_hanging_indent=True)
        assert check_author_format(para) is None, f"false positive on: {raw}"

    # Negative — genuine format errors should still fire R001
    bad = ReferenceParagraph(
        raw_text="Smith J. (2020). Test.",                   # missing comma
        runs=[], has_hanging_indent=True,
    )
    assert check_author_format(bad) is not None


def test_r008_missing_comma_before_ampersand():
    """R008: 'Smith, J. & Jones' should be 'Smith, J., & Jones'."""
    from app.rules.apa7 import check_comma_before_ampersand
    bad = ReferenceParagraph(
        raw_text="Smith, J. & Jones, A. (2020). Title. Journal, 1(1), 1–10.",
        runs=[], has_hanging_indent=True,
    )
    good = ReferenceParagraph(
        raw_text="Smith, J., & Jones, A. (2020). Title. Journal, 1(1), 1–10.",
        runs=[], has_hanging_indent=True,
    )
    assert check_comma_before_ampersand(bad) is not None
    assert check_comma_before_ampersand(good) is None


def test_r009_missing_period_after_year():
    """R009: '(2020) Title' should be '(2020). Title'."""
    from app.rules.apa7 import check_period_after_year
    bad = ReferenceParagraph(
        raw_text="Smith, J. (2020) Title here. Journal, 1(1).",
        runs=[], has_hanging_indent=True,
    )
    good = ReferenceParagraph(
        raw_text="Smith, J. (2020). Title here. Journal, 1(1).",
        runs=[], has_hanging_indent=True,
    )
    assert check_period_after_year(bad) is not None
    assert check_period_after_year(good) is None


def test_title_raw_preserves_original_case_and_punctuation():
    """Bug A: 'actual' in title-mismatch UI showed the lowercased, punctuation-
    stripped match-key instead of the user's original title. ReferenceEntry now
    keeps title_raw with original case and punctuation for UI display."""
    from app.services.citation_extractor import parse_reference_entries
    raw = "Yi, W., & Zhong, Y. (2024). The processing advantage of multiword sequences: A meta- analysis. Studies, 46(2), 427–452."
    entries = parse_reference_entries([raw])
    e = entries[0]
    # Preserved original
    assert e.title_raw == "The processing advantage of multiword sequences: A meta- analysis"
    # Normalized still works for fuzzy matching
    assert e.title_normalized == "the processing advantage of multiword sequences a meta analysis"


def test_intext_two_authors_with_ampersand_extracts_first_author():
    """Bug: in-text citation '(Christiansen & Chater, 2016)' was extracted as
    author='Christiansen & Chater', which never matched any reference entry's
    first_author_normalized='christiansen', causing false orphan warnings."""
    from app.services.citation_extractor import extract_intext_citations
    intexts = extract_intext_citations("As shown by (Christiansen & Chater, 2016)…")
    assert len(intexts) == 1
    i = intexts[0]
    assert i.author == "Christiansen"
    assert i.second_author == "Chater"
    assert i.n_authors == 2
    assert i.has_etal is False


def test_reference_entry_extracts_second_author():
    """ReferenceEntry now stores second_author_normalized to disambiguate
    two references with the same first author and year."""
    from app.services.citation_extractor import parse_reference_entries
    refs = parse_reference_entries([
        "Christiansen, M. H., & Chater, N. (2016). The now-or-never bottleneck. BBS, 39, e62.",
        "Smith, J. (2020). Solo paper. Journal, 1(1), 1–10.",
        "Smith, J., Jones, A., & Brown, B. (2020). Three-author paper. Journal, 1(1), 1–10.",
    ])
    assert refs[0].second_author_normalized == "chater"
    assert refs[1].second_author_normalized == ""   # single author
    assert refs[2].second_author_normalized == "jones"


def test_intext_matches_correct_reference_when_two_share_first_author():
    """Verify (Smith & Jones, 2020) matches the Smith+Jones reference, NOT
    the Smith+Wilson reference even though both have first author Smith + 2020."""
    from app.services.citation_extractor import extract_intext_citations, parse_reference_entries
    from app.services.report_builder import _check_intext
    refs = parse_reference_entries([
        "Smith, A., & Wilson, B. (2020). Paper A. Journal, 1(1), 1–10.",
        "Smith, A., & Jones, C. (2020). Paper B. Journal, 1(1), 1–10.",
    ])
    intexts = extract_intext_citations("Found by (Smith & Jones, 2020) recently.")
    issues = _check_intext(intexts[0], refs)
    orphan_issues = [i for i in issues if i.category == "orphan"]
    assert len(orphan_issues) == 0, "Should match Smith+Jones reference, not orphan"


def test_intext_format_regex_supports_unicode_surnames():
    """Bug: _INTEXT_FORMAT_RE used ASCII-only [A-Z][a-zA-Z]+, so any in-text
    citation with an accented surname (Lemhöfer, Ferré, Łukasiewicz) was
    flagged as 'does not conform to APA 7th format'. Fixed by extending
    character class to [A-ZÀ-Ɏ][a-zA-ZÀ-ɏ\\-]+."""
    from app.services.report_builder import _INTEXT_FORMAT_RE

    valid_unicode = [
        "(Lemhöfer & Broersma, 2012)",     # German ö
        "(Ferré, 2017)",                    # French é
        "(Łukasiewicz, 1999)",              # Polish Ł
        "(García-Pérez et al., 2020)",     # Spanish accent in compound
    ]
    for raw in valid_unicode:
        assert _INTEXT_FORMAT_RE.match(raw) is not None, f"false positive on: {raw}"

    # Genuine format errors should still fire
    invalid_format = [
        "(Smith 2020)",        # missing comma
        "(Smith, 20)",         # incomplete year
        "Smith, 2020",         # missing parens
    ]
    for raw in invalid_format:
        assert _INTEXT_FORMAT_RE.match(raw) is None, f"should fail on: {raw}"


def test_r013_year_mismatch_flagged_more_specifically_than_orphan():
    """R013: when in-text first author IS in the reference list but the year
    doesn't match, report a specific 'year mismatch' instead of a generic
    'orphan'. This is the common case where the user typo'd the year."""
    from app.services.citation_extractor import extract_intext_citations, parse_reference_entries
    from app.services.report_builder import _check_intext

    # The Dempsey 2025 / 2024 case from production
    refs = parse_reference_entries([
        "Dempsey, J., Christianson, K., & Van Dyke, J. A. (2024). Title. Reading and Writing, 1–22.",
    ])
    intexts = extract_intext_citations("see (Dempsey et al., 2025) for the algorithm")
    issues = _check_intext(intexts[0], refs)

    r013 = [i for i in issues if i.rule_id == "R013"]
    orphans = [i for i in issues if i.type == "orphan"]
    assert len(r013) == 1, "year mismatch should fire as R013"
    assert len(orphans) == 0, "should NOT also fire generic orphan"
    assert r013[0].expected == "(Dempsey et al., 2024)"
    assert r013[0].actual == "(Dempsey et al., 2025)"


def test_orphan_still_fires_when_author_not_in_references():
    """Genuine orphan (no reference with this author) should still report
    orphan, not R013."""
    from app.services.citation_extractor import extract_intext_citations, parse_reference_entries
    from app.services.report_builder import _check_intext

    refs = parse_reference_entries(["Smith, J. (2020). Paper. Journal, 1(1)."])
    intexts = extract_intext_citations("As (Ghost, 2099) noted")
    issues = _check_intext(intexts[0], refs)

    orphans = [i for i in issues if i.type == "orphan"]
    r013 = [i for i in issues if i.rule_id == "R013"]
    assert len(orphans) == 1
    assert len(r013) == 0


def test_r013_offers_multiple_years_when_author_has_multiple_works():
    """When the author has multiple works in the reference list, R013 should
    suggest all available years."""
    from app.services.citation_extractor import extract_intext_citations, parse_reference_entries
    from app.services.report_builder import _check_intext

    refs = parse_reference_entries([
        "Smith, J. (2020). Paper A. Journal, 1(1).",
        "Smith, J. (2022). Paper B. Journal, 1(1).",
    ])
    intexts = extract_intext_citations("As (Smith, 2021) noted")
    issues = _check_intext(intexts[0], refs)

    r013 = [i for i in issues if i.rule_id == "R013"]
    assert len(r013) == 1
    assert "2020" in r013[0].expected and "2022" in r013[0].expected


def test_r012_three_authors_without_et_al_flagged():
    """R012: APA 7th requires 'et al.' for 3+ authors in in-text citations."""
    from app.services.citation_extractor import extract_intext_citations, parse_reference_entries
    from app.services.report_builder import _check_intext
    refs = parse_reference_entries([
        "Smith, J., Jones, A., & Brown, B. (2020). Title. Journal, 1(1), 1–10.",
    ])

    # Bad: lists all 3 authors instead of using et al.
    intexts = extract_intext_citations("As shown by (Smith, Jones, & Brown, 2020)…")
    issues = _check_intext(intexts[0], refs)
    r012_issues = [i for i in issues if i.rule_id == "R012"]
    assert len(r012_issues) == 1

    # Good: uses et al.
    intexts = extract_intext_citations("As shown by (Smith et al., 2020)…")
    issues = _check_intext(intexts[0], refs)
    r012_issues = [i for i in issues if i.rule_id == "R012"]
    assert len(r012_issues) == 0


def test_r011_hyphen_with_adjacent_space_flagged():
    """R011: 'meta- analysis' or 'meta -analysis' (compound word with rogue
    space around the hyphen) should be flagged. Page ranges and clean usage
    should not."""
    from app.rules.apa7 import check_hyphen_spacing

    bad_cases = [
        "Smith, J. (2020). A meta- analysis of L2 acquisition. Journal, 1(1).",  # hyphen-space
        "Smith, J. (2020). A meta -analysis of L2 acquisition. Journal, 1(1).",  # space-hyphen
        "Smith, J. (2020). Studies in mixed- effects models. Journal, 1(1).",
    ]
    for raw in bad_cases:
        para = ReferenceParagraph(raw_text=raw, runs=[], has_hanging_indent=True)
        assert check_hyphen_spacing(para) is not None, f"R011 should fire on: {raw}"

    good_cases = [
        "Smith, J. (2020). A meta-analysis of L2 acquisition. Journal, 1(1), 1–10.",  # clean
        "Smith, J. (2020). Wiley-Blackwell handbook. Wiley.",                           # no space
        "Smith, J. (2020). Page range test. Journal, 1(1), 1- 10.",                     # page range (digits)
    ]
    for raw in good_cases:
        para = ReferenceParagraph(raw_text=raw, runs=[], has_hanging_indent=True)
        assert check_hyphen_spacing(para) is None, f"R011 false positive on: {raw}"


def test_r010_missing_comma_before_volume():
    """R010: 'Journal Name 12(3)' should be 'Journal Name, 12(3)'."""
    from app.rules.apa7 import check_comma_before_volume
    bad = ReferenceParagraph(
        raw_text="Smith, J. (2020). Title. Journal of Memory 62(1), 67–82.",
        runs=[], has_hanging_indent=True,
    )
    good = ReferenceParagraph(
        raw_text="Smith, J. (2020). Title. Journal of Memory, 62(1), 67–82.",
        runs=[], has_hanging_indent=True,
    )
    assert check_comma_before_volume(bad) is not None
    assert check_comma_before_volume(good) is None


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


def test_book_year_mismatch_uses_softer_alternative_edition_message():
    """Bug: Books commonly have multiple editions (e.g. Tomasello 2003 vs
    Crossref 2005 reprint). Hard 'Year mismatch' wording misled users into
    thinking they were wrong. For Crossref type=book*, use a softer
    'Possible alternative publication year' message that reminds the user
    to verify the edition."""
    entry = ReferenceEntry(
        raw_text="Tomasello, M. (2003). Constructing a language. Harvard University Press.",
        first_author_normalized="tomasello",
        year=2003,
        title_normalized="constructing a language a usagebased theory of language acquisition",
    )
    canonical = {
        "author": [{"family": "Tomasello", "given": "M"}],
        "published": {"date-parts": [[2005]]},
        "title": ["Constructing a language: A usage-based theory of language acquisition"],
        "container-title": ["Harvard University Press"],
        "type": "book",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    year_issues = [i for i in issues if i.field == "year"]
    assert len(year_issues) == 1
    assert year_issues[0].reason == "Possible alternative publication year"
    assert year_issues[0].detail is not None and "edition" in year_issues[0].detail.lower()
    # Values still shown so user can see the discrepancy
    assert year_issues[0].expected == "2005"
    assert year_issues[0].actual == "2003"


def test_journal_year_mismatch_still_uses_hard_year_mismatch_message():
    """Regression guard: journal articles keep the strict 'Year mismatch'
    wording — they typically have a single publication year, so year diff
    likely indicates a real citation error."""
    entry = ReferenceEntry(
        raw_text="Smith, J. (2018). Title. Journal, 1(1), 1–10.",
        first_author_normalized="smith",
        year=2018,
        title_normalized="title",
    )
    canonical = {
        "author": [{"family": "Smith", "given": "J"}],
        "published": {"date-parts": [[2020]]},
        "title": ["Title"],
        "container-title": ["Journal"],
        "type": "journal-article",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    year_issues = [i for i in issues if i.field == "year"]
    assert len(year_issues) == 1
    assert year_issues[0].reason == "Year mismatch"


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
    """Hard not-found shows plain manual-check guidance. (Historically this
    surfaced the raw per-database trail — 'Crossref: timeout · …' — but that
    read like a system error, so it was replaced by user request. The trail
    still exists in VerifyResult.not_found_reason for debugging.)"""
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
    assert not_found_issue.detail == "Please verify this reference manually"


# ── verifier ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_year_mismatched_preprint_not_flagged_as_ambiguous(tmp_path):
    """Bug: Crossref returns both the canonical 2013 book chapter AND an
    OSF preprint posted in 2017 with the same title for Markman (2013).
    Our 'near-exact title bypasses year' rule (for republication cases)
    let the 2017 preprint pollute the ambiguity check, so a clean match
    was getting flagged as 'Multiple papers found'. Fix: ambiguity now
    requires firm author+year+title match — preprints with different years
    don't count."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Markman, K. M. (2013). Conversational coherence...",
        first_author_normalized="markman",
        year=2013,
        title_normalized="conversational coherence in small group chat",
    )

    crossref_response = {
        "status": "ok",
        "message": {
            "items": [
                {  # canonical 2013 book chapter — the real match
                    "title": ["Conversational coherence in small group chat"],
                    "author": [{"family": "Markman"}],
                    "published": {"date-parts": [[2013]]},
                    "DOI": "10.1515/9783110214468.539",
                    "type": "book-chapter",
                },
                {  # 2017 OSF preprint with the same title — different year
                    "title": ["Conversational coherence in small group chat"],
                    "author": [{"family": "Markman"}],
                    "published": {"date-parts": [[2017]]},
                    "DOI": "10.31235/osf.io/g6vjf",
                    "type": "posted-content",
                },
                {  # unrelated chapter — title much lower, but author+year_missing
                    "title": ["A Close Look at Online Collaboration"],
                    "author": [{"family": "Markman"}],
                    "published": {"date-parts": [[0]]},
                    "DOI": "10.4018/abc",
                    "type": "book-chapter",
                },
            ]
        }
    }
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=crossref_response)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None
    assert result.found is True
    assert result.ambiguous is False, "preprint with different year and unrelated chapter shouldn't trigger ambiguity"


@pytest.mark.asyncio
async def test_software_citation_skipped_and_marked_yellow(tmp_path):
    """Software citations with APA [Computer software] tag aren't in academic
    databases — should be classified pre-flight and surfaced as yellow
    'Manual verification required', not red 'Reference not found'."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import verify_reference, _classify_reference
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="QSR International Pty Ltd. (2020). NVivo (Version 12) [Computer software]. https://qsrinternational.com",
        first_author_normalized="qsr international pty ltd",
        year=2020,
        title_normalized="nvivo",
    )
    assert _classify_reference(entry) == "software"
    result = await verify_reference(entry, db_path)
    assert result.found is False
    assert "software" in (result.not_found_reason or "")

    para = ReferenceParagraph(raw_text=entry.raw_text, runs=[(entry.raw_text, True)], has_hanging_indent=True)
    report = build_report(
        report_id="t", filename="t.docx", full_text=entry.raw_text,
        intext_citations=[],
        reference_entries=[entry],
        reference_paragraphs=[para],
        verify_results=[result],
    )
    citation = report.citations[0]
    assert citation.status == "warning"
    not_found = next(i for i in citation.issues if i.type == "not_found")
    assert not_found.severity == "yellow"


@pytest.mark.asyncio
async def test_proceedings_without_doi_skipped_and_marked_yellow(tmp_path):
    """Conference proceedings without a DOI (e.g. CogSci papers on
    mindmodeling.org, ACL Anthology PDFs) shouldn't be flagged as red
    'Reference not found' — they're legitimately not in Crossref/OpenAlex."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import verify_reference, _classify_reference
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text=(
            "McCauley, S. M., Isbilen, E. S., & Christiansen, M. H. (2017). "
            "Chunking ability shapes sentence processing at multiple levels of "
            "abstraction. In G. Gunzelmann (Ed.), Proceedings of the 39th Annual "
            "Conference of the Cognitive Science Society (pp. 2681–2686). "
            "Cognitive Science Society. https://cogsci.mindmodeling.org/2017/papers/0507/paper0507.pdf"
        ),
        first_author_normalized="mccauley",
        year=2017,
        title_normalized="chunking ability shapes sentence processing at multiple levels of abstraction",
    )
    assert _classify_reference(entry) == "proceedings"
    result = await verify_reference(entry, db_path)
    assert result.found is False
    assert "proceedings" in (result.not_found_reason or "")


def test_proceedings_with_doi_still_goes_through_normal_verification():
    """Proceedings WITH a DOI (e.g. CHI, NeurIPS proceedings registered on
    Crossref) should NOT be classified — they can be verified normally."""
    from app.services.verifier import _classify_reference

    entry = ReferenceEntry(
        raw_text="Smith, J. (2020). Title. In Proceedings of CHI 2020. ACM. https://doi.org/10.1145/3313831.3376432",
        first_author_normalized="smith",
        year=2020,
        title_normalized="title",
        doi="10.1145/3313831.3376432",
    )
    assert _classify_reference(entry) is None


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


# ── book chapter handling (R014–R016 + journal-check skip) ────────────────────

_MACWHINNEY_CHAPTER = (
    "MacWhinney, B. (2008). A unified model. In Handbook of cognitive "
    "linguistics and second language acquisition (pp. 351-381). Routledge."
)


def test_chapter_journal_check_skipped_for_book_chapter_type():
    """Bug: a valid book chapter raised 'Journal name does not match'. For
    Crossref type=book-chapter, container-title is the *book* title (and a
    generic chapter title often resolves to a different same-named book), so
    the journal check must be skipped."""
    entry = ReferenceEntry(
        raw_text=_MACWHINNEY_CHAPTER,
        first_author_normalized="macwhinney",
        year=2008,
        title_normalized="a unified model",
    )
    canonical = {
        "author": [{"family": "MacWhinney", "given": "B"}],
        "published": {"date-parts": [[2008]]},
        "title": ["A unified model"],
        "container-title": ["Handbook of Bilingualism"],  # different same-named book
        "type": "book-chapter",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    assert [i for i in issues if i.field == "journal"] == []


def test_r014_chapter_missing_editors_flagged():
    """R014: a chapter without (Eds.) editors is flagged; the MacWhinney
    example is missing editors and should fire R014 only (not R015/R016)."""
    from app.services.apa_validator import validate_reference_paragraph
    para = ReferenceParagraph(raw_text=_MACWHINNEY_CHAPTER, runs=[], has_hanging_indent=True)
    rule_ids = {i.rule_id for i in validate_reference_paragraph(para)}
    assert "R014" in rule_ids
    assert "R015" not in rule_ids  # pages already use "pp."
    assert "R016" not in rule_ids  # no editor block present

    good = ReferenceParagraph(
        raw_text=(
            "MacWhinney, B. (2008). A unified model. In P. Robinson & N. C. "
            "Ellis (Eds.), Handbook of cognitive linguistics and second "
            "language acquisition (pp. 351-381). Routledge."
        ),
        runs=[], has_hanging_indent=True,
    )
    from app.rules.apa7 import check_chapter_editors
    assert check_chapter_editors(good) is None


def test_r014_not_triggered_for_journal_article():
    """Guard: journal articles (no '. In <Book>' source marker) never trigger
    the chapter rules."""
    from app.rules.apa7 import (
        check_chapter_editors, check_chapter_page_format, check_chapter_editor_order,
    )
    para = ReferenceParagraph(
        raw_text="Smith, J. (2020). A study of things. Journal of Things, 1(1), 1-10.",
        runs=[], has_hanging_indent=True,
    )
    assert check_chapter_editors(para) is None
    assert check_chapter_page_format(para) is None
    assert check_chapter_editor_order(para) is None


def test_r015_chapter_bare_page_range_flagged():
    """R015: chapter pages must use 'pp.' — '(351-381)' is flagged,
    '(pp. 351-381)' is accepted."""
    from app.rules.apa7 import check_chapter_page_format
    bad = ReferenceParagraph(
        raw_text=(
            "MacWhinney, B. (2008). A unified model. In P. Robinson (Ed.), "
            "Handbook of cognitive linguistics (351-381). Routledge."
        ),
        runs=[], has_hanging_indent=True,
    )
    good = ReferenceParagraph(
        raw_text=(
            "MacWhinney, B. (2008). A unified model. In P. Robinson (Ed.), "
            "Handbook of cognitive linguistics (pp. 351-381). Routledge."
        ),
        runs=[], has_hanging_indent=True,
    )
    assert check_chapter_page_format(bad) is not None
    assert check_chapter_page_format(good) is None


def test_r016_chapter_editor_wrong_order_flagged():
    """R016: editors use 'F. M. Last' order — 'Robinson, P., & Ellis, N. C.'
    is flagged, 'P. Robinson & N. C. Ellis' is accepted."""
    from app.rules.apa7 import check_chapter_editor_order
    bad = ReferenceParagraph(
        raw_text=(
            "MacWhinney, B. (2008). A unified model. In Robinson, P., & Ellis, "
            "N. C. (Eds.), Handbook of cognitive linguistics (pp. 351-381). Routledge."
        ),
        runs=[], has_hanging_indent=True,
    )
    good = ReferenceParagraph(
        raw_text=(
            "MacWhinney, B. (2008). A unified model. In P. Robinson & N. C. "
            "Ellis (Eds.), Handbook of cognitive linguistics (pp. 351-381). Routledge."
        ),
        runs=[], has_hanging_indent=True,
    )
    assert check_chapter_editor_order(bad) is not None
    assert check_chapter_editor_order(good) is None


def test_r003_chapter_uses_book_title_wording():
    """R003 wording is chapter-aware: a chapter with no italic runs says
    'Book title should be in italics', while a journal article keeps the
    'Journal name' wording."""
    from app.rules.apa7 import check_journal_italic
    chapter = ReferenceParagraph(raw_text=_MACWHINNEY_CHAPTER, runs=[], has_hanging_indent=True)
    article = ReferenceParagraph(
        raw_text="Smith, J. (2020). A study. Journal of Things, 1(1), 1-10.",
        runs=[], has_hanging_indent=True,
    )
    chapter_issue = check_journal_italic(chapter)
    article_issue = check_journal_italic(article)
    assert chapter_issue is not None and "Book title" in chapter_issue.reason
    assert article_issue is not None and "Journal name" in article_issue.reason


def test_crossref_title_html_markup_stripped():
    """Bug: Crossref/JATS titles embed markup (e.g. '<b>lmerTest</b> Package').
    Shown raw in 'expected' and, after _norm mangles the tags into 'b...b'
    tokens, the fuzzy score drops below 95 → false 'Title does not match'.
    Markup must be stripped before scoring and display."""
    from app.services.verifier import _strip_markup
    assert _strip_markup("<b>lmerTest</b> Package: Tests") == "lmerTest Package: Tests"

    entry = ReferenceEntry(
        raw_text=(
            "Kuznetsova, A., Brockhoff, P. B., & Christensen, R. H. B. (2017). "
            "lmerTest Package: Tests in Linear Mixed Effects Models. Journal of "
            "Statistical Software, 82(13), 1–26. https://doi.org/10.18637/jss.v082.i13"
        ),
        first_author_normalized="kuznetsova",
        year=2017,
        title_normalized="lmertest package tests in linear mixed effects models",
        doi="10.18637/jss.v082.i13",
    )
    canonical = {
        "author": [{"family": "Kuznetsova", "given": "A"}],
        "published": {"date-parts": [[2017]]},
        "title": ["<b>lmerTest</b> Package: Tests in Linear Mixed Effects Models"],
        "container-title": ["Journal of Statistical Software"],
        "type": "journal-article",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    # Title now matches once markup is gone → no false title mismatch.
    assert [i for i in issues if i.field == "title"] == []
    # And no raw markup leaks into any displayed expected value.
    for i in issues:
        if i.expected:
            assert "<" not in i.expected and ">" not in i.expected


# ── R003 is journal-aware: detect the journal name, then check its italics ────

def test_r003_not_fired_when_reference_has_no_journal():
    """Bug: R003 fired 'Journal name should be in italics' on a software /
    R-package citation that has no journal at all. R003 must first detect a
    journal name (Vol(Issue), pages structure); absent that, it does not fire."""
    from app.rules.apa7 import check_journal_italic, _journal_name
    rpkg = (
        "Kuznetsova, A., Brockhoff, P. B., Christensen, R. H. B., & Jensen, "
        "S. P. (2017). lmerTest: Tests in linear mixed effects models "
        "(R package version 3.1-3)."
    )
    assert _journal_name(rpkg) is None
    para = ReferenceParagraph(raw_text=rpkg, runs=[], has_hanging_indent=True)
    assert check_journal_italic(para) is None


def test_r003_fired_when_journal_present_but_not_italic():
    """The proper JSS form HAS a journal; with no italic run, R003 fires."""
    from app.rules.apa7 import check_journal_italic, _journal_name
    jss = (
        "Kuznetsova, A., Brockhoff, P. B., & Christensen, R. H. B. (2017). "
        "lmerTest package: Tests in linear mixed effects models. Journal of "
        "Statistical Software, 82(13), 1–26."
    )
    assert _journal_name(jss) == "Journal of Statistical Software"
    para = ReferenceParagraph(raw_text=jss, runs=[], has_hanging_indent=True)
    assert check_journal_italic(para) is not None


def test_r003_passes_when_journal_name_is_italicised():
    """When the journal name itself is in an italic run, R003 passes."""
    from app.rules.apa7 import check_journal_italic
    runs = [
        ("Kuznetsova, A. (2017). lmerTest package: Tests. ", False),
        ("Journal of Statistical Software", True),
        (", 82(13), 1–26.", False),
    ]
    raw = "".join(t for t, _ in runs)
    para = ReferenceParagraph(raw_text=raw, runs=runs, has_hanging_indent=True)
    assert check_journal_italic(para) is None


def test_r003_fired_when_wrong_element_is_italicised():
    """Precise check: italicising the article *title* instead of the journal
    still fails — the journal name is not italic."""
    from app.rules.apa7 import check_journal_italic
    runs = [
        ("Kuznetsova, A. (2017). ", False),
        ("lmerTest package: Tests", True),  # title italic (wrong)
        (". Journal of Statistical Software, 82(13), 1–26.", False),
    ]
    raw = "".join(t for t, _ in runs)
    para = ReferenceParagraph(raw_text=raw, runs=runs, has_hanging_indent=True)
    assert check_journal_italic(para) is not None


# ── docx italic via style inheritance (not just direct toggle) ────────────────

class _FakeFont:
    def __init__(self, italic): self.italic = italic
class _FakeStyle:
    def __init__(self, italic=None): self.font = _FakeFont(italic)
class _FakeRun:
    def __init__(self, text, italic=None, style_italic=None):
        self.text = text
        self.italic = italic
        self.style = _FakeStyle(style_italic)
class _FakePara:
    def __init__(self, style_italic=None): self.style = _FakeStyle(style_italic)


def test_effective_italic_direct_toggle():
    """Direct Cmd+I on the run — same as old behaviour, still works."""
    from app.services.docx_parser import _effective_italic
    assert _effective_italic(_FakeRun("x", italic=True), _FakePara()) is True
    assert _effective_italic(_FakeRun("x", italic=False), _FakePara()) is False


def test_effective_italic_from_character_style():
    """Bug: italic via a character style (e.g. 'Emphasis') was lost — run.italic
    is None, so the old `r.italic is True` check returned False, R003 then
    falsely reported the journal name was not italic."""
    from app.services.docx_parser import _effective_italic
    run = _FakeRun("Behavior Research Methods", italic=None, style_italic=True)
    assert _effective_italic(run, _FakePara()) is True


def test_effective_italic_from_paragraph_style():
    """Italic inherited from the paragraph style also counts."""
    from app.services.docx_parser import _effective_italic
    run = _FakeRun("x", italic=None, style_italic=None)
    assert _effective_italic(run, _FakePara(style_italic=True)) is True


def test_effective_italic_direct_off_overrides_inherited():
    """Explicit italic=False on the run wins over any inherited italic."""
    from app.services.docx_parser import _effective_italic
    run = _FakeRun("x", italic=False, style_italic=True)
    assert _effective_italic(run, _FakePara(style_italic=True)) is False


def test_effective_italic_defaults_to_false():
    """No italic anywhere → False."""
    from app.services.docx_parser import _effective_italic
    assert _effective_italic(_FakeRun("x"), _FakePara()) is False


def test_r003_passes_when_italic_comes_from_character_style():
    """End-to-end: a journal-name run that gets italic from a character style
    is now recorded as italic by _effective_italic → R003 passes (no false
    'Journal name should be in italics' warning on the Alzahrani case)."""
    from app.rules.apa7 import check_journal_italic
    runs = [
        ("Alzahrani, A. (2024). LexArabic: A receptive vocabulary size test "
         "to estimate Arabic proficiency. ", False),
        ("Behavior Research Methods", True),       # style-inherited italic
        (", 56(6), 5529-5556. https://doi.org/10.3758/s13428-023-02286-z", False),
    ]
    raw = "".join(t for t, _ in runs)
    para = ReferenceParagraph(raw_text=raw, runs=runs, has_hanging_indent=True)
    assert check_journal_italic(para) is None


def test_effective_italic_walks_base_style_chain():
    """Bug: a character style (e.g. Emphasis) may itself have italic=None and
    inherit italic from its base_style. The original one-level lookup missed
    that case → R003 still falsely reported missing italic on legitimate docs.
    _style_chain_italic now walks the base_style ancestors."""
    from app.services.docx_parser import _effective_italic
    # Build a 2-level style chain: child has italic=None, base has italic=True.
    base = _FakeStyle(italic=True)
    child = _FakeStyle(italic=None)
    child.base_style = base
    run = _FakeRun("Behavior Research Methods", italic=None)
    run.style = child
    assert _effective_italic(run, _FakePara()) is True


def test_reference_citation_carries_runs_with_italic():
    """Citation.runs is populated for reference citations so the UI can
    re-render italics that exist in the source Word document."""
    from app.models.schemas import TextRun

    # Minimal Citation construction via build_report would need full fixtures;
    # exercise the field directly to lock in the schema + serialisation shape.
    runs = [TextRun(text="Plain ", italic=False), TextRun(text="Journal", italic=True)]
    payload = [r.model_dump() for r in runs]
    assert payload == [
        {"text": "Plain ", "italic": False},
        {"text": "Journal", "italic": True},
    ]


def test_r003_passes_when_italic_runs_are_split_at_whitespace():
    """Bug: Word frequently splits an italic span at whitespace — the words
    are italic, the spaces between them are not. _italic_text used to join
    with "" so "Behavior Research Methods" became "BehaviorResearchMethods"
    and the journal-name substring check failed, even though the user saw a
    fully italicised journal name in Word and in our UI."""
    from app.rules.apa7 import check_journal_italic
    runs = [
        ("Alzahrani, A. (2024). LexArabic: A receptive vocabulary size test "
         "to estimate Arabic proficiency. ", False),
        ("Behavior", True),         # ← Word split the italic span
        (" ", False),
        ("Research", True),
        (" ", False),
        ("Methods", True),
        (", 56(6), 5529-5556. https://doi.org/10.3758/s13428-023-02286-z", False),
    ]
    raw = "".join(t for t, _ in runs)
    para = ReferenceParagraph(raw_text=raw, runs=runs, has_hanging_indent=True)
    assert check_journal_italic(para) is None


def test_r003_journal_name_handles_nbsp_between_sentences():
    """Bug: Word inserts NBSP (\\xa0) between sentences. _journal_name's
    `before.rfind('. ')` is ASCII-only and misses '. \\xa0', so it falls
    back to the previous '. ' (after the year) and swallows the article
    title into the extracted journal name. That huge string then never
    matches the italic text, and R003 falsely fires on legitimate refs."""
    from app.rules.apa7 import _journal_name, check_journal_italic
    text = (
        "Alzahrani, A. (2024). LexArabic: A receptive vocabulary size test "
        "to estimate Arabic proficiency.\xa0Behavior Research Methods,"
        "\xa056(6), 5529-5556. https://doi.org/10.3758/s13428-023-02286-z"
    )
    assert _journal_name(text) == "Behavior Research Methods"

    # End-to-end: with the correct journal name extracted, the journal-name
    # italic check passes when the italic run carries that exact phrase.
    runs = [
        ("Alzahrani, A. (2024). LexArabic: A receptive vocabulary size test "
         "to estimate Arabic proficiency.\xa0", False),
        ("Behavior Research Methods", True),
        (",\xa0", False),
        ("56", True),
        ("(6), 5529-5556. https://doi.org/10.3758/s13428-023-02286-z", False),
    ]
    para = ReferenceParagraph(raw_text=text, runs=runs, has_hanging_indent=True)
    assert check_journal_italic(para) is None


@pytest.mark.asyncio
@respx.mock
async def test_jiang_2018_does_not_match_jiang_2011_with_similar_title(tmp_path):
    """Bug: 'Jiang, N. (2018). Second language processing: An introduction.'
    was being matched to Jiang's earlier 2011 book 'Introducing Second
    Language Processing' — same author, near-exact (but not identical) title,
    7-year drift. The result was a misleading 'expected = Introducing Second
    Language Processing' warning. With the tightened bypass (5-yr cap on
    near-exact titles) the 2011 book is no longer accepted."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Jiang, N. (2018). Second language processing: An introduction. Routledge.",
        first_author_normalized="jiang",
        year=2018,
        title_normalized="second language processing an introduction",
    )
    near_match_wrong_year = {
        "status": "ok",
        "message": {
            "items": [{
                "title": ["Introducing Second Language Processing"],   # near-exact, not 100
                "author": [{"family": "Jiang"}],
                "published": {"date-parts": [[2011]]},                # 7-year drift
                "DOI": "10.1/wrong",
            }]
        }
    }
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=near_match_wrong_year)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_near_exact_title_accepted_within_reprint_window(tmp_path):
    """Boundary: a title that normalises to a genuinely near-exact score in the
    95–99 band (not 100) with a small year drift (<5 yrs) is still accepted —
    covers online-first / next-year reprints whose Crossref title has a minor
    spelling difference from the cited form.

    (Codex second-challenge condition 2: the earlier version relied on a
    punctuation-only difference, but `_norm` strips punctuation, so both scores
    were 100 and the 95–99 branch was never exercised. 'collocations' vs the
    typo 'colocations' normalises to set==sort≈98.)"""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    from app.services.verifier import _norm
    from rapidfuzz import fuzz
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Smith, J. (2020). A useful study of collocations.",
        first_author_normalized="smith",
        year=2020,
        title_normalized="a useful study of collocations",
    )
    cand_title = "A Useful Study of Colocations"   # single-letter typo
    # Guard the guard: assert this data really lands in the 95–99 band so the
    # test keeps exercising the near-exact branch if the norm/scorer changes.
    _s = fuzz.token_set_ratio(entry.title_normalized, _norm(cand_title))
    assert 95 <= _s < 100, f"test data no longer in 95-99 band: {_s}"

    near_in_window = {
        "status": "ok",
        "message": {
            "items": [{
                "title": [cand_title],
                "author": [{"family": "Smith"}],
                "published": {"date-parts": [[2023]]}, # 3-year drift, within window
                "DOI": "10.1/ok",
            }]
        }
    }
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=near_in_window)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None and result.found is True


@pytest.mark.asyncio
@respx.mock
async def test_book_cite_prefers_book_record_over_its_chapter_records(tmp_path):
    """Bug: 'Jiang, N. (2018). Second language processing: An introduction.
    Routledge.' is a book cite. Crossref DOI-registers each chapter of that
    book separately and returns the chapters BEFORE the parent-book record.
    The verifier was picking the first chapter ('Introducing Second Language
    Processing') as the authoritative record, producing a misleading
    'expected = Introducing Second Language Processing' title warning.
    With the chapter-deprioritisation, the parent-book record wins."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Jiang, N. (2018). Second language processing: An introduction. Routledge.",
        first_author_normalized="jiang",
        year=2018,
        title_normalized="second language processing an introduction",
    )
    # Mirrors the real Crossref response: chapters first, then the book.
    crossref_payload = {
        "status": "ok",
        "message": {
            "items": [
                {
                    "title": ["Introducing Second Language Processing"],
                    "author": [{"family": "Jiang"}],
                    "published": {"date-parts": [[2018]]},
                    "type": "book-chapter",
                    "DOI": "10.1/chapter1",
                },
                {
                    "title": ["Second Language Processing"],
                    "author": [{"family": "Jiang"}],
                    "published": {"date-parts": [[2018]]},
                    "type": "book",
                    "DOI": "10.1/the-book",
                },
                {
                    "title": ["Phonological Processing in L2"],
                    "author": [{"family": "Jiang"}],
                    "published": {"date-parts": [[2018]]},
                    "type": "book-chapter",
                    "DOI": "10.1/chapter2",
                },
            ]
        }
    }
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=crossref_payload)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None
    assert result.found is True
    # The book record (type=book) wins, NOT the first chapter.
    assert result.canonical["DOI"] == "10.1/the-book"
    assert result.canonical["title"][0] == "Second Language Processing"


@pytest.mark.asyncio
@respx.mock
async def test_chapter_cite_still_matches_chapter_record(tmp_path):
    """Guard: when the entry IS formatted as a chapter cite ('. In <Book>'),
    we DON'T deprioritise book-chapter candidates — that's the right answer."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text=(
            "Smith, J. (2020). Chapter title. In Editor, E. (Ed.), Book "
            "title (pp. 1-20). Publisher."
        ),
        first_author_normalized="smith",
        year=2020,
        title_normalized="chapter title",
    )
    crossref_payload = {
        "status": "ok",
        "message": {
            "items": [{
                "title": ["Chapter Title"],
                "author": [{"family": "Smith"}],
                "published": {"date-parts": [[2020]]},
                "type": "book-chapter",
                "DOI": "10.1/chap",
            }]
        }
    }
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=crossref_payload)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None and result.found is True
    assert result.canonical["DOI"] == "10.1/chap"


# ── R017: editors marker must include period ─────────────────────────────────

def test_r017_eds_without_period_flagged():
    """Bug: a chapter cite with '(Eds)' instead of '(Eds.)' was triggering
    R014 'missing editors' — misleading, because editors ARE named, just
    with the wrong punctuation. R017 flags the punctuation specifically;
    R014 stays for the genuinely-missing case."""
    from app.rules.apa7 import (
        check_chapter_editors,
        check_chapter_editors_period,
    )
    bad = ReferenceParagraph(
        raw_text=(
            "Ellis, N. C. (2008). Usage-based and form-focused language "
            "acquisition. In P. Robinson & N. Ellis (Eds), Handbook of "
            "cognitive linguistics and second language acquisition "
            "(pp. 372-405). New York: Routledge."
        ),
        runs=[], has_hanging_indent=True,
    )
    # R014 should NOT fire (editors are present, just punctuation-flawed)
    assert check_chapter_editors(bad) is None
    # R017 should fire (period missing)
    r017 = check_chapter_editors_period(bad)
    assert r017 is not None
    assert r017.rule_id == "R017"

    good = ReferenceParagraph(
        raw_text=(
            "Ellis, N. C. (2008). Usage-based and form-focused language "
            "acquisition. In P. Robinson & N. Ellis (Eds.), Handbook of "
            "cognitive linguistics and second language acquisition "
            "(pp. 372-405). New York: Routledge."
        ),
        runs=[], has_hanging_indent=True,
    )
    assert check_chapter_editors_period(good) is None


def test_r017_skipped_when_no_editors_marker_at_all():
    """R014 owns 'no editors' — R017 only fires when the marker is present
    but malformed, so we don't double-warn."""
    from app.rules.apa7 import check_chapter_editors_period
    para = ReferenceParagraph(
        raw_text=(
            "MacWhinney, B. (2008). A unified model. In Handbook of "
            "cognitive linguistics (pp. 351-381). Routledge."
        ),
        runs=[], has_hanging_indent=True,
    )
    assert check_chapter_editors_period(para) is None


# ── chapter cite matched to parent book: skip title mismatch ─────────────────

def test_chapter_cite_skipped_title_check_when_doi_returns_parent_book():
    """Bug: Ellis 2008 chapter cited a parent-book DOI (Routledge often only
    DOIs the whole book, not individual chapters). _compare_fields was
    comparing the entry's chapter title against the book's title and
    flagging 'Title does not match' — misleading, since the DOI is correct.
    The check is now skipped when entry is a chapter cite and the matched
    record is type=book."""
    entry = ReferenceEntry(
        raw_text=(
            "Ellis, N. C. (2008). Usage-based and form-focused language "
            "acquisition. In P. Robinson & N. Ellis (Eds.), Handbook of "
            "cognitive linguistics and second language acquisition "
            "(pp. 372-405). New York: Routledge."
        ),
        first_author_normalized="ellis",
        year=2008,
        title_normalized="usage based and form focused language acquisition",
        doi="10.4324/9780203938560",
    )
    canonical = {
        "title": ["Handbook of Cognitive Linguistics and Second Language Acquisition"],
        "author": [],
        "editor": [{"family": "Robinson"}, {"family": "Ellis"}],
        "published": {"date-parts": [[2008]]},
        "type": "book",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    assert [i for i in issues if i.field == "title"] == []


def test_non_chapter_cite_still_flags_title_mismatch_against_book():
    """Regression guard: when the entry is NOT a chapter cite, we still
    compare titles even if the matched record is type=book — otherwise we'd
    silently miss real title mismatches on book references."""
    entry = ReferenceEntry(
        raw_text="Smith, J. (2020). The wrong title. Publisher.",
        first_author_normalized="smith",
        year=2020,
        title_normalized="the wrong title",
    )
    canonical = {
        "title": ["A Completely Different Book"],
        "author": [{"family": "Smith"}],
        "published": {"date-parts": [[2020]]},
        "type": "book",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    title_issues = [i for i in issues if i.field == "title"]
    assert len(title_issues) == 1


# ── Issues of AI_ref_Checker.docx batch (2026-07-04) ──────────────────────────

def test_journal_name_extracted_when_title_ends_with_question_mark():
    """Bug (r3 Durrant): _journal_name only recognised '. ' as the end of the
    article title. Titles ending with '?' ('…make use of collocations?')
    made it fall back to the sentence after the year, swallowing the whole
    title into the extracted journal name — which then broke both the
    journal-mismatch comparison and the R003 italic containment check."""
    from app.rules.apa7 import _journal_name
    raw = (
        "Durrant, P., & Schmitt, N. (2009). To what extent do native and "
        "non-native writers make use of collocations? International Review "
        "of Applied Linguistics, 47(2), 157-177. https://doi.org/10.1515/iral.2009.007"
    )
    assert _journal_name(raw) == "International Review of Applied Linguistics"


def test_r003_passes_for_italic_journal_after_question_mark_title():
    """Bug (r3): with the journal name wrongly extracted (title included),
    R003 reported 'should be in italics' even though the journal WAS italic."""
    from app.rules.apa7 import check_journal_italic
    runs = [
        ("Durrant, P., & Schmitt, N. (2009). To what extent do native and "
         "non-native writers make use of collocations? ", False),
        ("International Review of Applied Linguistics", True),
        (", 47(2), 157-177. https://doi.org/10.1515/iral.2009.007", False),
    ]
    raw = "".join(t for t, _ in runs)
    para = ReferenceParagraph(raw_text=raw, runs=runs, has_hanging_indent=True)
    assert check_journal_italic(para) is None


def test_journal_abbreviated_name_not_flagged_as_mismatch():
    """Bug (r3/r4): the journal check compared the candidate against the WHOLE
    reference text, diluting the score to ~77 and flagging legitimate short
    forms. 'International Review of Applied Linguistics' is a subset of the
    official 'IRAL - International Review of Applied Linguistics in Language
    Teaching' → token_set 100 → no warning."""
    entry = ReferenceEntry(
        raw_text=(
            "Farghal, M., & Obiedat, H. (1995). Collocations: A neglected "
            "variable in EFL. International Review of Applied Linguistics, "
            "33(4), 315-331. https://doi.org/10.1515/iral.1995.33.4.315"
        ),
        first_author_normalized="farghal",
        year=1995,
        title_normalized="collocations a neglected variable in efl",
        doi="10.1515/iral.1995.33.4.315",
    )
    canonical = {
        "author": [{"family": "Farghal", "given": "M"}],
        "published": {"date-parts": [[1995]]},
        "title": ["Collocations: a neglected variable in EFL"],
        "container-title": ["IRAL - International Review of Applied Linguistics in Language Teaching"],
        "type": "journal-article",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    assert [i for i in issues if i.field == "journal"] == []


def test_journal_hyphen_spacing_variant_not_flagged():
    """Bug (Peters): 'ITL-International Journal…' (no spaces around hyphen)
    vs official 'ITL - International Journal…' was flagged. Dashes are now
    normalised to spaces before comparing."""
    entry = ReferenceEntry(
        raw_text=(
            "Peters, E. (2018). The effect of out-of-class exposure to English "
            "language media on learners' vocabulary knowledge. ITL-International "
            "Journal of Applied Linguistics, 169(1), 142-168."
        ),
        first_author_normalized="peters",
        year=2018,
        title_normalized="the effect of outofclass exposure to english language media on learners vocabulary knowledge",
    )
    canonical = {
        "author": [{"family": "Peters", "given": "E"}],
        "published": {"date-parts": [[2018]]},
        "title": ["The effect of out-of-class exposure to English language media on learners' vocabulary knowledge"],
        "container-title": ["ITL - International Journal of Applied Linguistics"],
        "type": "journal-article",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    assert [i for i in issues if i.field == "journal"] == []


def test_journal_mismatch_fills_actual_with_extracted_name():
    """Bug (r3/r4 UI): the journal-mismatch issue only set `expected`, so the
    'actual' column rendered empty. It now carries the extracted journal name."""
    entry = ReferenceEntry(
        raw_text="Smith, J. (2020). A study. Journal of Wrong Things, 1(1), 1-10.",
        first_author_normalized="smith",
        year=2020,
        title_normalized="a study",
    )
    canonical = {
        "author": [{"family": "Smith", "given": "J"}],
        "published": {"date-parts": [[2020]]},
        "title": ["A study"],
        "container-title": ["Journal of Right Things"],
        "type": "journal-article",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    j = [i for i in issues if i.field == "journal"]
    assert len(j) == 1
    assert j[0].actual == "Journal of Wrong Things"
    assert j[0].expected == "Journal of Right Things"


def test_r018_journal_capitalisation_flagged():
    """Enhancement (Plonsky): 'Language learning' passed silently although the
    authoritative journal name is 'Language Learning'. Case-only differences
    now raise a yellow R018 format hint (never a content mismatch)."""
    entry = ReferenceEntry(
        raw_text=(
            "Plonsky, L., & Oswald, F. L. (2014). How big is “big”? "
            "Interpreting effect sizes in L2 research. Language learning, "
            "64(4), 878–912. https://doi.org/10.1111/lang.12079"
        ),
        first_author_normalized="plonsky",
        year=2014,
        title_normalized="how big is big interpreting effect sizes in l2 research",
        doi="10.1111/lang.12079",
    )
    canonical = {
        "author": [{"family": "Plonsky", "given": "L"}],
        "published": {"date-parts": [[2014]]},
        "title": ["How Big Is “Big”? Interpreting Effect Sizes in L2 Research"],
        "container-title": ["Language Learning"],
        "type": "journal-article",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    r018 = [i for i in issues if i.rule_id == "R018"]
    assert len(r018) == 1
    assert r018[0].expected == "Language Learning"
    assert r018[0].actual == "Language learning"
    assert r018[0].severity == "yellow"
    # No content-level journal mismatch alongside it
    assert [i for i in issues if i.field == "journal" and i.type == "field_mismatch"] == []


def test_author_multiword_surname_not_flagged_in_compare_fields():
    """Bug (r14 Van Vu): Crossref stores family='Vu', given='Duy Van' for
    'Van Vu, D.'. _compare_fields used strict equality → false 'Author name
    mismatch (expected Vu, actual Van Vu)'. It now reuses the verifier's
    lenient _author_surname_match (last word equal)."""
    entry = ReferenceEntry(
        raw_text=(
            "Van Vu, D., & Peters, E. (2022). Incidental learning of collocations "
            "from meaningful input. Studies in Second Language Acquisition, "
            "44(3), 685-707. https://doi.org/10.1017/S0272263121000462"
        ),
        first_author_normalized="van vu",
        year=2022,
        title_normalized="incidental learning of collocations from meaningful input",
        doi="10.1017/S0272263121000462",
    )
    canonical = {
        "author": [{"family": "Vu", "given": "Duy Van"}, {"family": "Peters", "given": "Elke"}],
        "published": {"date-parts": [[2022]]},
        "title": ["Incidental learning of collocations from meaningful input"],
        "container-title": ["Studies in Second Language Acquisition"],
        "type": "journal-article",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    assert [i for i in issues if i.field == "author"] == []


def test_r001_multiword_surname_not_flagged():
    """Bug (r14 Van Vu): R001's regex required a single-word surname, so
    'Van Vu, D.' (and 'Pekarek Doehler, S.') were flagged as bad author
    format. The extractor has accepted multi-word surnames all along —
    the format rule now agrees."""
    from app.rules.apa7 import check_author_format
    for raw in [
        "Van Vu, D., & Peters, E. (2022). Title. Journal, 44(3), 685-707.",
        "Pekarek Doehler, S. (2018). Title. Journal, 3(2), 173-207.",
        "Vu, D. (2022). Title. Journal, 44(3), 685-707.",
    ]:
        para = ReferenceParagraph(raw_text=raw, runs=[], has_hanging_indent=True)
        assert check_author_format(para) is None, raw
    # Still fires on genuinely bad format
    bad = ReferenceParagraph(
        raw_text="van vu, D. (2022). Title. Journal, 44(3), 685-707.",
        runs=[], has_hanging_indent=True,
    )
    assert check_author_format(bad) is not None


def test_r011_reports_actual_offending_snippet_not_canned_example():
    """Bug (Granger & Bestgen): R011's expected/actual were hard-coded to
    'meta-analysis' / 'meta- analysis'. A user whose reference contained
    'bigram- based' (line-break artifact) saw 'meta- analysis' in the UI and
    couldn't tell what actually triggered the warning. The issue now carries
    the real matched snippet."""
    from app.rules.apa7 import check_hyphen_spacing
    para = ReferenceParagraph(
        raw_text=(
            "Granger, S., & Bestgen, Y. (2014). The use of collocations by "
            "intermediate vs. advanced non-native writers: A bigram- based "
            "study. International Review of Applied Linguistics in Language "
            "Teaching, 52(3), 229-252. https://doi.org/10.1515/iral-2014-0011"
        ),
        runs=[], has_hanging_indent=True,
    )
    issue = check_hyphen_spacing(para)
    assert issue is not None
    assert issue.actual == "bigram- based"
    assert issue.expected == "bigram-based"

    # Clean hyphens elsewhere in the same text must not trigger at all.
    clean = ReferenceParagraph(
        raw_text=(
            "Granger, S., & Bestgen, Y. (2014). The use of collocations by "
            "intermediate vs. advanced non-native writers: A bigram-based "
            "study. International Review of Applied Linguistics in Language "
            "Teaching, 52(3), 229-252. https://doi.org/10.1515/iral-2014-0011"
        ),
        runs=[], has_hanging_indent=True,
    )
    assert check_hyphen_spacing(clean) is None


# ── merged references + encyclopedia chapter (Schmitt+Wolter chimera) ─────────

_SCHMITT_WOLTER_MERGED = (
    "Schmitt, N., Sonbul, S., Vilkaitė-Lozdienė, L., & Macis, M. (2019). "
    "Formulaic language and collocation. In C. A. Chapelle (Ed.), The "
    "encyclopedia of applied linguistics. John Wiley & Sons. "
    "https://doi.org/10.1002/9781405198431.wbeal0433.pub2 "
    "Wolter, B., & Gyllstad, H. (2013). Frequency of input and L2 "
    "collocational processing: A comparison of congruent and incongruent "
    "collocations. Studies in Second Language Acquisition, 35(3), 451-482. "
    "https://doi.org/10.1017/S0272263113000107"
)

_SCHMITT_CANONICAL = {
    "author": [{"family": "Schmitt", "given": "Norbert"}],
    "published": {"date-parts": [[2019]]},
    "title": ["Formulaic Language and Collocation"],
    "container-title": ["The Encyclopedia of Applied Linguistics"],
    "type": "other",   # Crossref types encyclopedia entries as 'other'
}


def test_merged_references_flagged_and_field_comparison_skipped():
    """Bug (Schmitt+Wolter): two references glued into one entry (lost
    paragraph break in Word). Verification matched ref #1 (encyclopedia)
    while the journal check extracted the journal from ref #2 (SSLA) —
    producing a chimera warning whose expected/actual came from two
    different, individually-correct references. Merged entries now get a
    dedicated warning and field comparison is skipped."""
    entry = ReferenceEntry(
        raw_text=_SCHMITT_WOLTER_MERGED,
        first_author_normalized="schmitt",
        year=2019,
        title_normalized="formulaic language and collocation",
        doi="10.1002/9781405198431.wbeal0433.pub2",
    )
    para = ReferenceParagraph(raw_text=_SCHMITT_WOLTER_MERGED, runs=[(_SCHMITT_WOLTER_MERGED, True)], has_hanging_indent=True)
    vr = VerifyResult(found=True, exact_match=True, canonical=_SCHMITT_CANONICAL)
    report = build_report("t", "t.docx", _SCHMITT_WOLTER_MERGED, [], [entry], [para], [vr])
    ref = report.citations[0]
    reasons = [i.reason for i in ref.issues]
    assert "Two references appear to be merged into one entry" in reasons
    # No chimera journal mismatch
    assert [i for i in ref.issues if i.field == "journal"] == []


def test_single_reference_not_flagged_as_merged():
    """Guard: a normal single reference (one year, one DOI) must not trigger
    the merged-references warning."""
    from app.services.report_builder import _looks_like_merged_references
    assert _looks_like_merged_references(
        "Smith, J. (2020). A study. Journal, 1(1), 1-10. https://doi.org/10.1/abc"
    ) is False
    assert _looks_like_merged_references(_SCHMITT_WOLTER_MERGED) is True


def test_chapter_formatted_cite_skips_journal_check_even_when_type_other():
    """Bug (Schmitt encyclopedia): Crossref types encyclopedia entries as
    'other', so the book-chapter type skip missed them and the journal check
    compared the encyclopedia title against an extracted journal name. The
    skip now also honours the cite's own chapter format ('. In Editor (Ed.),')."""
    raw = (
        "Schmitt, N., Sonbul, S., Vilkaitė-Lozdienė, L., & Macis, M. (2019). "
        "Formulaic language and collocation. In C. A. Chapelle (Ed.), The "
        "encyclopedia of applied linguistics. John Wiley & Sons. "
        "https://doi.org/10.1002/9781405198431.wbeal0433.pub2"
    )
    entry = ReferenceEntry(
        raw_text=raw,
        first_author_normalized="schmitt",
        year=2019,
        title_normalized="formulaic language and collocation",
        doi="10.1002/9781405198431.wbeal0433.pub2",
    )
    vr = VerifyResult(found=True, exact_match=True, canonical=_SCHMITT_CANONICAL)
    issues = _compare_fields(entry, vr)
    assert [i for i in issues if i.field == "journal"] == []


def test_r019_chapter_missing_pages_flagged_when_record_has_pages():
    """R019 fires only for genuine edited-book chapters (type=book-chapter)
    whose Crossref record has pages. Online reference-work entries
    (encyclopedias — Crossref type 'other') are exempt: APA 7's own examples
    omit page numbers for those, and their `page` field is per-entry PDF
    pagination ('1-10'), so a reminder there is noise."""
    raw = (
        "Schmitt, N., Sonbul, S., Vilkaitė-Lozdienė, L., & Macis, M. (2019). "
        "Formulaic language and collocation. In C. A. Chapelle (Ed.), The "
        "encyclopedia of applied linguistics. John Wiley & Sons. "
        "https://doi.org/10.1002/9781405198431.wbeal0433.pub2"
    )
    entry = ReferenceEntry(
        raw_text=raw,
        first_author_normalized="schmitt",
        year=2019,
        title_normalized="formulaic language and collocation",
        doi="10.1002/9781405198431.wbeal0433.pub2",
    )
    canonical = dict(_SCHMITT_CANONICAL, type="book-chapter", page="1-10")
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    r019 = [i for i in issues if i.rule_id == "R019"]
    assert len(r019) == 1
    assert r019[0].expected == "(pp. 1-10)"
    assert "1-10" in (r019[0].detail or "")

    # With (pp. …) present → no flag
    entry_with_pp = ReferenceEntry(
        raw_text=raw.replace(
            "applied linguistics. John Wiley",
            "applied linguistics (pp. 1-10). John Wiley",
        ),
        first_author_normalized="schmitt",
        year=2019,
        title_normalized="formulaic language and collocation",
        doi="10.1002/9781405198431.wbeal0433.pub2",
    )
    assert [i for i in _compare_fields(entry_with_pp, vr) if i.rule_id == "R019"] == []

    # Record without page (unpaginated online entry) → no flag
    vr_no_page = VerifyResult(found=True, exact_match=True, canonical=_SCHMITT_CANONICAL)
    assert [i for i in _compare_fields(entry, vr_no_page) if i.rule_id == "R019"] == []

    # Encyclopedia entry (Crossref type='other') WITH per-entry pagination →
    # exempt. This is the Schmitt case the user challenged: omitting pages
    # for an online reference work is valid APA 7.
    vr_encyc = VerifyResult(
        found=True, exact_match=True,
        canonical=dict(_SCHMITT_CANONICAL, page="1-10"),
    )
    assert [i for i in _compare_fields(entry, vr_encyc) if i.rule_id == "R019"] == []

    # Journal article with pages (non-chapter) → no flag
    article = ReferenceEntry(
        raw_text="Smith, J. (2020). A study. Journal, 1(1), 1-10.",
        first_author_normalized="smith",
        year=2020,
        title_normalized="a study",
    )
    vr_article = VerifyResult(found=True, exact_match=True, canonical={
        "author": [{"family": "Smith", "given": "J"}],
        "published": {"date-parts": [[2020]]},
        "title": ["A study"],
        "container-title": ["Journal"],
        "type": "journal-article",
        "page": "1-10",
    })
    assert [i for i in _compare_fields(article, vr_article) if i.rule_id == "R019"] == []


# ── PDF digit artifact defeats new-reference detection ────────────────────────

def _docx_bytes(*paragraph_texts):
    """Build a minimal in-memory .docx with the given paragraphs."""
    import io as _io
    from docx import Document as _Doc
    d = _Doc()
    for t in paragraph_texts:
        d.add_paragraph(t)
    buf = _io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_leading_digit_junk_starts_new_reference():
    """Bug (References check01.docx): the PDF-copied line '12Wolter, B., &
    Gyllstad…' starts with a stray page number glued to the surname. The
    new-reference regex requires an uppercase first char, so the paragraph
    was treated as a continuation and merged into the previous (Schmitt)
    entry — producing the Encyclopedia/SSLA chimera. The parser now strips
    a short leading digit run glued to an uppercase letter and re-tests."""
    from app.services.docx_parser import parse_docx
    data = _docx_bytes(
        "References",
        "Schmitt, N., Sonbul, S., & Macis, M. (2019). Formulaic language and",
        "collocation. In C. A. Chapelle (Ed.), The encyclopedia of applied linguistics. John Wiley",
        "& Sons. https://doi.org/10.1002/9781405198431.wbeal0433.pub2",
        "12Wolter, B., & Gyllstad, H. (2013). Frequency of input and L2 collocational processing: A",
        "comparison of congruent and incongruent collocations. Studies in Second Language",
        "Acquisition, 35(3), 451-482. https://doi.org/10.1017/S0272263113000107",
    )
    parsed = parse_docx(data)
    assert len(parsed.reference_paragraphs) == 2
    schmitt, wolter = parsed.reference_paragraphs
    assert schmitt.raw_text.startswith("Schmitt, N.")
    assert "Wolter" not in schmitt.raw_text
    # junk digits stripped from both raw_text and runs
    assert wolter.raw_text.startswith("Wolter, B.")
    assert wolter.runs[0][0].startswith("Wolter")


def test_leading_digits_not_glued_to_uppercase_still_merge_as_continuation():
    """Guard: continuation lines that merely START with digits (page ranges,
    years, '451-482. https…') are NOT new references and must keep merging."""
    from app.services.docx_parser import parse_docx
    data = _docx_bytes(
        "References",
        "Smith, J. (2020). A long title that wraps across lines. Journal of Things,",
        "12(3), 451-482. https://doi.org/10.1017/S0272263113000107",
    )
    parsed = parse_docx(data)
    assert len(parsed.reference_paragraphs) == 1
    assert "451-482" in parsed.reference_paragraphs[0].raw_text


def test_title_with_internal_abbreviation_periods_extracted_fully():
    """Bug (Gledhill 1972): titles containing single-letter abbreviations
    ('V.C.C. rock climbing guide…', 'U.S. foreign policy…') were truncated at
    the first internal period — the extracted title became just 'V', so
    Crossref/OpenAlex/Open Library were all queried with a garbage title and
    indexed works could falsely come back 'not found'."""
    from app.services.citation_extractor import parse_reference_entries

    entries = parse_reference_entries([
        "Gledhill, A., & Gledhill, G. (1972). V.C.C. rock climbing guide to "
        "the Northern Grampians. Victorian Climbing Club.",
        "Smith, J. (2020). U.S. foreign policy in the age of A.I. tools. "
        "Journal of Things, 12(3), 45-67.",
        # guards: ordinary titles unchanged
        "Jiang, N. (2018). Second language processing: An introduction. Routledge.",
        "Durrant, P., & Schmitt, N. (2009). To what extent do native and "
        "non-native writers make use of collocations? International Review "
        "of Applied Linguistics, 47(2), 157-177.",
        # title ending in a multi-letter acronym must still stop at its period
        "Lee, K. (2021). Effects of using AI. Journal of Things, 1(1), 1-10.",
    ])
    assert entries[0].title_normalized == "vcc rock climbing guide to the northern grampians"
    assert entries[0].title_raw == "V.C.C. rock climbing guide to the Northern Grampians"
    assert entries[1].title_normalized == "us foreign policy in the age of ai tools"
    assert entries[2].title_normalized == "second language processing an introduction"
    assert entries[3].title_normalized == "to what extent do native and nonnative writers make use of collocations"
    assert entries[4].title_normalized == "effects of using ai"
# ── hyphenated surnames + Open Library book fallback (r8/r16 batch) ───────────

def test_author_surname_match_strips_hyphens():
    """Bug (r16 Wenger-Trayner): _norm strips hyphens from Crossref names
    ('Wenger-Trayner' → 'wengertrayner') but the extractor's
    first_author_normalized keeps them ('wenger-trayner'), so every
    hyphenated first author without a DOI failed the surname match and the
    reference went red 'not found' even when Crossref had it."""
    from app.services.verifier import _author_surname_match, _norm
    assert _author_surname_match(_norm("Wenger-Trayner"), "wenger-trayner") is True
    assert _author_surname_match(_norm("Al-Gahtani"), "al-gahtani") is True
    # unrelated names still rejected
    assert _author_surname_match(_norm("Smith"), "wenger-trayner") is False


@pytest.mark.asyncio
@respx.mock
async def test_r16_hyphenated_editor_book_found_in_crossref(tmp_path):
    """r16: 'Learning in landscapes of practice' (Eds. Wenger-Trayner et al.)
    IS in Crossref (type=book, 2014, editors) — it was rejected only by the
    hyphen mismatch above. With the fix it verifies."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text=(
            "Wenger-Trayner, E., Fenton-O'Creevy, M., Hutchinson, S., Kubiak, C., "
            "& Wenger-Trayner, B. (Eds.). (2015). Learning in landscapes of "
            "practice: Boundaries, identity, and knowledgeability in "
            "practice-based learning. Routledge."
        ),
        first_author_normalized="wenger-trayner",
        year=2015,
        title_normalized="learning in landscapes of practice boundaries identity and knowledgeability in practicebased learning",
    )
    crossref_payload = {
        "status": "ok",
        "message": {
            "items": [{
                "title": ["Learning in Landscapes of Practice"],
                "editor": [{"family": "Wenger-Trayner", "given": "Etienne"}],
                "published": {"date-parts": [[2014]]},   # 1-yr drift, within tolerance
                "type": "book",
                "DOI": "10.4324/9781315777122",
            }]
        }
    }
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=crossref_payload)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None and result.found is True


@pytest.mark.asyncio
@respx.mock
async def test_openlibrary_fallback_finds_predoi_book(tmp_path):
    """r8 Labov (1972): pre-DOI books were never registered in Crossref (only
    1975 book *reviews* titled 'Sociolinguistic patterns. By William Labov'
    show up, correctly rejected on author). Open Library has the real book —
    verify_reference now falls through to it for book-shaped references."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import verify_reference
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Labov, W. (1972). Sociolinguistic patterns. Philadelphia: University of Pennsylvania Press.",
        first_author_normalized="labov",
        year=1972,
        title_normalized="sociolinguistic patterns",
    )
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"message": {"items": []}})
    )
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get("https://openlibrary.org/search.json").mock(
        return_value=httpx.Response(200, json={"docs": [{
            "title": "Sociolinguistic Patterns",
            "author_name": ["William Labov"],
            "first_publish_year": 1973,
            "publish_year": [1972, 1973, 1980],
            "publisher": ["University of Pennsylvania Press"],
        }]})
    )
    result = await verify_reference(entry, db_path)
    assert result.found is True
    assert result.source == "openlibrary"
    # closest edition year picked → no spurious year warning downstream
    assert result.canonical["published"]["date-parts"] == [[1972]]


@pytest.mark.asyncio
@respx.mock
async def test_openlibrary_skipped_for_journal_shaped_refs(tmp_path):
    """Guard: journal-article-shaped references (Vol(Issue), pages) never hit
    Open Library — it only knows books, and querying it with article titles
    would produce junk matches."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import verify_reference
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Smith, J. (2020). A study of things. Journal of Things, 12(3), 45-67.",
        first_author_normalized="smith",
        year=2020,
        title_normalized="a study of things",
    )
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"message": {"items": []}})
    )
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    ol_route = respx.get("https://openlibrary.org/search.json").mock(
        return_value=httpx.Response(200, json={"docs": []})
    )
    result = await verify_reference(entry, db_path)
    assert result.found is False
    assert ol_route.call_count == 0
    assert "Open Library" not in (result.not_found_reason or "")
def test_org_with_period_and_nd_and_lowercase_brand_start_new_references():
    """Bug (References check03.docx): three references merged into one entry.
    'The jamovi project. (2024)' has a period between the org name and the
    year parens; 'theCrag. (n.d.)' starts lowercase AND uses (n.d.) instead
    of a year — both failed _REF_START_RE and merged into the preceding
    Sánchez-Hernández entry."""
    from app.services.docx_parser import parse_docx
    data = _docx_bytes(
        "References",
        "Sánchez-Hernández, A. & Alcón-Soler, E. (2019). Pragmatic gains in the "
        "study abroad context. Journal of Pragmatics 146, 54–71.",
        "The jamovi project. (2024). jamovi (Version 2.5) [Computer software]. "
        "https://www.jamovi.org",
        "theCrag. (n.d.). Glossary. Retrieved May 28, 2026, from "
        "https://www.thecrag.com/en/article/glossary",
    )
    parsed = parse_docx(data)
    assert len(parsed.reference_paragraphs) == 3
    assert parsed.reference_paragraphs[0].raw_text.startswith("Sánchez-Hernández")
    assert parsed.reference_paragraphs[1].raw_text.startswith("The jamovi project.")
    assert parsed.reference_paragraphs[2].raw_text.startswith("theCrag.")


def test_lowercase_multiword_continuation_line_still_merges():
    """Guard: the lowercase-brand alternation requires a single word followed
    by a period — wrapped continuation lines starting with lowercase words
    (even ones containing a parenthesised year) keep merging."""
    from app.services.docx_parser import parse_docx
    data = _docx_bytes(
        "References",
        "Smith, J. (2020). A review of",
        "the study (2019) found effects, 12(3), 45-67. https://doi.org/10.1/x",
    )
    parsed = parse_docx(data)
    assert len(parsed.reference_paragraphs) == 1
    assert "the study (2019)" in parsed.reference_paragraphs[0].raw_text


def test_hard_not_found_detail_leads_with_manual_check_guidance():
    """UX (Gledhill guidebook): the raw per-database trail ('Crossref: score
    too low · OpenAlex: no results · …') read like a system error. Hard
    not-found details now lead with 'Please verify this reference manually'
    while keeping the technical trail; soft categories (software/web/
    proceedings) keep their own wording."""
    entry = ReferenceEntry(
        raw_text="Gledhill, A., & Gledhill, G. (1972). V.C.C. rock climbing guide to the Northern Grampians. Victorian Climbing Club.",
        first_author_normalized="gledhill",
        year=1972,
        title_normalized="vcc rock climbing guide to the northern grampians",
    )
    para = ReferenceParagraph(raw_text=entry.raw_text, runs=[], has_hanging_indent=True)
    vr = VerifyResult(
        found=False,
        not_found_reason="Crossref: score too low or author/year mismatch · OpenAlex: no results · Open Library: no results",
    )
    report = build_report("t", "t.docx", entry.raw_text, [], [entry], [para], [vr])
    issue = [i for i in report.citations[0].issues if i.type == "not_found"][0]
    assert issue.severity == "red"
    assert issue.detail == "Please verify this reference manually"

    # Soft category (software) keeps its own wording, no double guidance
    vr_soft = VerifyResult(found=False, not_found_reason="software citation — not in academic databases")
    report2 = build_report("t", "t.docx", entry.raw_text, [], [entry], [para], [vr_soft])
    issue2 = [i for i in report2.citations[0].issues if i.type == "not_found"][0]
    assert issue2.severity == "yellow"
    assert not issue2.detail.startswith("Please verify this reference manually")


# ── journal metadata rules: R010 no-issue form + R020 missing vol/pages ───────

def test_r010_missing_comma_before_volume_without_issue_number():
    """Bug (Leuckert 2024): 'Dictionaries Journal of the Dictionary Society
    of North America 45, 373–401' — missing comma between journal name and
    volume. R010 only matched the '12(3)' issue form, so journals without
    issue numbers slipped through and the cite passed as 'APA format
    correct'."""
    from app.rules.apa7 import check_comma_before_volume
    bad = ReferenceParagraph(
        raw_text=(
            "Leuckert, S. (2024). Stop Focusing on What the Dictionary Says! "
            "Meta-Perspectives on Lexicographical Resources of Mountaineering "
            "English on Reddit. Dictionaries Journal of the Dictionary Society "
            "of North America 45, 373–401."
        ),
        runs=[], has_hanging_indent=True,
    )
    good = ReferenceParagraph(
        raw_text=(
            "Leuckert, S. (2024). Stop Focusing on What the Dictionary Says! "
            "Meta-Perspectives on Lexicographical Resources of Mountaineering "
            "English on Reddit. Dictionaries: Journal of the Dictionary Society "
            "of North America, 45, 373–401."
        ),
        runs=[], has_hanging_indent=True,
    )
    # dates ('Retrieved May 28, 2026, from …') must not false-trigger
    date_ref = ReferenceParagraph(
        raw_text="theCrag. (n.d.). Glossary. Retrieved May 28, 2026, from https://example.com",
        runs=[], has_hanging_indent=True,
    )
    assert check_comma_before_volume(bad) is not None
    assert check_comma_before_volume(good) is None
    assert check_comma_before_volume(date_ref) is None


def test_r020_journal_article_missing_volume_and_pages_flagged():
    """Bug (Gyllstad 2024): a journal-article cite with NO volume/issue/pages
    (and a stray publisher name) passed as 'APA format correct'. The verified
    Crossref record (10.1075/itl.23005.gyl) has volume 176, issue 1, pages
    1-43 — when the record proves the metadata exists but the cite has no
    numeric metadata at all, R020 now reminds the user."""
    entry = ReferenceEntry(
        raw_text=(
            "Gyllstad, H., Kupisch, T., & Lloyd-Smith, A. (2024). Development "
            "and initial validation of a yes/no vocabulary test for North Sámi. "
            "Drawing on item response theory and signal detection theory. "
            "International Journal of Applied Linguistics. John Benjamins "
            "Publishing Company."
        ),
        first_author_normalized="gyllstad",
        year=2024,
        title_normalized="development and initial validation of a yesno vocabulary test for north sámi",
    )
    canonical = {
        "author": [{"family": "Gyllstad", "given": "Henrik"}],
        "published": {"date-parts": [[2024]]},
        "title": ["Development and initial validation of a yes/no vocabulary test for North Sámi"],
        "container-title": ["ITL - International Journal of Applied Linguistics"],
        "type": "journal-article",
        "volume": "176", "issue": "1", "page": "1-43",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    r020 = [i for i in issues if i.rule_id == "R020"]
    assert len(r020) == 1
    assert r020[0].expected == "176(1), 1-43"
    assert r020[0].severity == "yellow"

    # Guard: cite WITH volume/pages (even malformed comma) → no R020
    entry_with_meta = ReferenceEntry(
        raw_text=(
            "Leuckert, S. (2024). Stop Focusing! Dictionaries Journal of the "
            "Dictionary Society of North America 45, 373–401."
        ),
        first_author_normalized="leuckert",
        year=2024,
        title_normalized="stop focusing",
    )
    vr2 = VerifyResult(found=True, exact_match=True, canonical={
        "author": [{"family": "Leuckert", "given": "S"}],
        "published": {"date-parts": [[2024]]},
        "title": ["Stop Focusing!"],
        "container-title": ["Dictionaries: Journal of the Dictionary Society of North America"],
        "type": "journal-article",
        "volume": "45", "page": "373-401",
    })
    assert [i for i in _compare_fields(entry_with_meta, vr2) if i.rule_id == "R020"] == []

    # Guard: books never trigger R020
    book_entry = ReferenceEntry(
        raw_text="Jiang, N. (2018). Second language processing: An introduction. Routledge.",
        first_author_normalized="jiang",
        year=2018,
        title_normalized="second language processing an introduction",
    )
    vr3 = VerifyResult(found=True, exact_match=True, canonical={
        "author": [{"family": "Jiang", "given": "N"}],
        "published": {"date-parts": [[2018]]},
        "title": ["Second Language Processing"],
        "container-title": [],
        "type": "book",
        "page": "1-300",
    })
    assert [i for i in _compare_fields(book_entry, vr3) if i.rule_id == "R020"] == []


# ── narrative in-text citations ('Smith (2020) argued…') ─────────────────────

def test_narrative_intext_citations_extracted():
    """Gap: _INTEXT_RE only matched parenthetical '(Smith, 2020)'. Narrative
    citations — the dominant form in academic prose — were invisible, so
    orphan/year-mismatch checks silently skipped them."""
    from app.services.citation_extractor import extract_intext_citations
    text = (
        "Smith (2020) argued that input matters. According to Van Vu and "
        "Peters (2022), collocations are learned incidentally. Gyllstad's "
        "(2007) test was influential, and Wolter et al. (2013) agreed. "
        "Smith, Jones, and Brown (2019) listed everyone."
    )
    cites = extract_intext_citations(text)
    by_author = {(c.author, c.year): c for c in cites}

    c1 = by_author[("Smith", 2020)]
    assert c1.narrative and c1.n_authors == 1

    c2 = by_author[("Van Vu", 2022)]           # multi-word surname
    assert c2.narrative and c2.second_author == "Peters" and c2.n_authors == 2

    c3 = by_author[("Gyllstad", 2007)]         # possessive stripped
    assert c3.narrative

    c4 = by_author[("Wolter", 2013)]
    assert c4.narrative and c4.has_etal

    c5 = by_author[("Smith", 2019)]            # comma list, 3 authors
    assert c5.narrative and c5.n_authors == 3 and c5.second_author == "Jones"


def test_narrative_extraction_guards():
    """Lowercase words, digit-containing tokens, and parenthetical cites must
    not produce narrative matches (no double counting, no false orphans)."""
    from app.services.citation_extractor import extract_intext_citations
    text = (
        "As shown in the study (2019), effects were large. See Table 1 "
        "(2020) for details. Prior work (Smith, 2020) found the same."
    )
    cites = extract_intext_citations(text)
    # only the parenthetical (Smith, 2020) — 'the study'/'Table 1' rejected
    assert len(cites) == 1
    assert cites[0].author == "Smith" and not cites[0].narrative


def test_narrative_intext_matches_reference_without_format_warning():
    """A correct narrative cite must produce zero issues — before the fix it
    either produced nothing (invisible) or, had it been extracted, would have
    failed the parenthetical format regex."""
    from app.services.report_builder import _check_intext
    from app.services.citation_extractor import extract_intext_citations
    refs = [ReferenceEntry(
        raw_text="Smith, J. (2020). A study. Journal, 1(1), 1-10.",
        first_author_normalized="smith", year=2020, title_normalized="a study",
    )]
    cite = extract_intext_citations("Smith (2020) argued this.")[0]
    assert cite.narrative
    assert _check_intext(cite, refs) == []


def test_narrative_intext_orphan_and_year_mismatch():
    """Narrative cites now participate in orphan / R013 checks, with
    narrative-form expected values ('Smith (2019)', not '(Smith, 2019)')."""
    from app.services.report_builder import _check_intext
    from app.services.citation_extractor import extract_intext_citations
    refs = [ReferenceEntry(
        raw_text="Smith, J. (2019). A study. Journal, 1(1), 1-10.",
        first_author_normalized="smith", year=2019, title_normalized="a study",
    )]
    # year mismatch → R013 with narrative expected form
    cite = extract_intext_citations("Smith (2020) argued this.")[0]
    issues = _check_intext(cite, refs)
    r013 = [i for i in issues if i.rule_id == "R013"]
    assert len(r013) == 1 and r013[0].expected == "Smith (2019)"

    # unknown author → orphan
    cite2 = extract_intext_citations("Nobody (2020) claimed otherwise.")[0]
    issues2 = _check_intext(cite2, refs)
    assert any(i.type == "orphan" for i in issues2)


def test_narrative_r012_uses_narrative_expected_form():
    """3+ authors listed in a narrative cite → R012 with 'Smith et al. (2019)'
    as the expected form (not the parenthetical '(Smith et al., 2019)')."""
    from app.services.report_builder import _check_intext
    from app.services.citation_extractor import extract_intext_citations
    refs = [ReferenceEntry(
        raw_text="Smith, J., Jones, A., & Brown, B. (2019). A study. Journal, 1(1), 1-10.",
        first_author_normalized="smith", year=2019, title_normalized="a study",
        second_author_normalized="jones",
    )]
    cite = extract_intext_citations("Smith, Jones, and Brown (2019) listed everyone.")[0]
    issues = _check_intext(cite, refs)
    r012 = [i for i in issues if i.rule_id == "R012"]
    assert len(r012) == 1 and r012[0].expected == "Smith et al. (2019)"


def test_narrative_sentence_adverbs_not_absorbed_into_author():
    """Bug found during e2e: 'However, Gyllstad's (2024)' parsed the sentence
    adverb as the first author (comma list without an and-group is not a
    valid APA narrative form). 'Both Smith and Jones (2019)' similarly glued
    'Both' onto the surname via the multi-word pattern."""
    from app.services.citation_extractor import extract_intext_citations

    c = extract_intext_citations("However, Gyllstad's (2024) work extends this.")[0]
    assert c.author == "Gyllstad" and c.n_authors == 1

    c2 = extract_intext_citations("Both Smith and Jones (2019) agree.")[0]
    assert c2.author == "Smith" and c2.second_author == "Jones"


# ── References check06 batch ──────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_subset_title_with_year_gap_rejected_by_token_sort_gate(tmp_path):
    """Bug (Bandura 1986): a book-chapter candidate whose (shorter) title is a
    *subset* of the cited book's title yields token_set=100, and the
    any-year-gap exact bypass then accepted it despite a 16-year gap. The
    candidate here is a book-chapter (NOT an article), so the article-type
    guard does NOT apply — this isolates and verifies the token_sort_ratio
    gate: the length-sensitive score (~75 for this subset) is below the
    threshold, so the bypass is denied.
    (Codex cross-review #7: the earlier version used a journal-article
    candidate, which the type guard rejected regardless of token_sort, so it
    never actually exercised this gate.)"""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Bandura, A. (1986). Social foundations of thought and action: A social cognitive theory. Prentice-Hall.",
        first_author_normalized="bandura",
        year=1986,
        title_normalized="social foundations of thought and action a social cognitive theory",
    )
    # A 2002 book-chapter whose (shorter) title is a subset of the book title:
    # token_set=100 but token_sort~75. Not an article type, so only the
    # token_sort gate can reject it.
    crossref_payload = {"status": "ok", "message": {"items": [{
        "title": ["Social Foundations of Thought and Action"],
        "author": [{"family": "Bandura"}],
        "published": {"date-parts": [[2002]]},
        "type": "book-chapter",
        "page": "94-106",
        "DOI": "10.1/chapter",
    }]}}
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=crossref_payload)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is None  # token_sort gate rejects → falls through to Open Library


@pytest.mark.asyncio
@respx.mock
async def test_book_form_rejects_journal_article_via_type_guard(tmp_path):
    """Companion to the token_sort test: a same-author journal article whose
    title *contains* the book title (Bandura 1997 → 1990 AIDS article) is
    rejected by the article-type guard for a book-form entry."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Bandura, A. (1997). Self-efficacy: The exercise of control. W. H. Freeman.",
        first_author_normalized="bandura",
        year=1997,
        title_normalized="selfefficacy the exercise of control",
    )
    crossref_payload = {"status": "ok", "message": {"items": [{
        "title": ["Perceived self-efficacy in the exercise of control over AIDS infection"],
        "author": [{"family": "Bandura"}],
        "published": {"date-parts": [[1990]]},
        "type": "journal-article",
        "volume": "13", "page": "9-17",
        "DOI": "10.1/wrong",
    }]}}
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=crossref_payload)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_book_review_type_rejected_for_book_form_entry(tmp_path):
    """Codex cross-review #3c: a book *review* (type=review) shares the book's
    exact title and is the classic false match. 'review'/'preprint'/etc. are
    now in the article-type guard, so a book-form entry rejects them."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_openalex
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Bandura, A. (1997). Self-efficacy: The exercise of control. W. H. Freeman.",
        first_author_normalized="bandura", year=1997,
        title_normalized="selfefficacy the exercise of control",
    )
    openalex_payload = {"results": [{
        "title": "Self-Efficacy: The Exercise of Control",   # identical title, a review
        "authorships": [{"author": {"display_name": "Albert Bandura"}}],
        # gap 3 → not year_ok, forces the near-window bypass path where the
        # article-type guard actually runs (a gap ≤1 would pass via year_ok,
        # which the guard deliberately doesn't cover — see Codex #1 backlog).
        "publication_year": 1994,
        "doi": "https://doi.org/10.1/review",
        "type": "review",
        "host_venue": {"display_name": "Some Journal"},
    }]}
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=openalex_payload)
    )
    result, _ = await _search_openalex(httpx.AsyncClient(), entry, db_path)
    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_book_form_reference_still_matches_book_candidate(tmp_path):
    """Guard: a book-form entry still matches a genuine book record."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_crossref
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Jiang, N. (2018). Second language processing: An introduction. Routledge.",
        first_author_normalized="jiang", year=2018,
        title_normalized="second language processing an introduction",
    )
    payload = {"status": "ok", "message": {"items": [{
        "title": ["Second Language Processing"],
        "author": [{"family": "Jiang"}],
        "published": {"date-parts": [[2018]]},
        "type": "book", "DOI": "10.1/book",
    }]}}
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json=payload)
    )
    result, _ = await _search_crossref(httpx.AsyncClient(), entry, db_path)
    assert result is not None and result.found


def test_r020_not_flagged_for_article_number_volume_form():
    """Bug (Wang & Sun 2020): 'System, 95, 102366' is the modern article-number
    form (volume, article no., no page range). _VOL_PAGES_PRESENT_RE only knew
    page ranges and vol(issue), so R020 falsely fired even though the metadata
    is present and matches the record."""
    entry = ReferenceEntry(
        raw_text="Wang, C., & Sun, T. (2020). Relationship between self-efficacy and language proficiency: A meta-analysis. System, 95, 102366.",
        first_author_normalized="wang", year=2020,
        title_normalized="relationship between selfefficacy and language proficiency a metaanalysis",
    )
    canonical = {
        "author": [{"family": "Wang", "given": "C"}],
        "published": {"date-parts": [[2020]]},
        "title": ["Relationship between self-efficacy and language proficiency: A meta-analysis"],
        "container-title": ["System"],
        "type": "journal-article", "volume": "95", "page": "102366",
    }
    vr = VerifyResult(found=True, exact_match=True, canonical=canonical)
    issues = _compare_fields(entry, vr)
    assert [i for i in issues if i.rule_id == "R020"] == []


def test_merged_detection_ignores_orphan_doi_line():
    """Bug (Horwitz 1986): a single reference that already has its own DOI
    picked up a trailing orphan DOI line (another entry's DOI that wrapped
    onto its own line and merged into the previous entry). Two DOIs but one
    (YYYY) — must NOT be flagged as merged. Merged detection now keys only on
    2+ parenthetical years."""
    from app.services.report_builder import _looks_like_merged_references
    horwitz = (
        "Horwitz, E. K., Horwitz, M. B., & Cope, J. (1986). Foreign language "
        "classroom anxiety. The Modern Language Journal, 70(2), 125-132. "
        "https://doi.org/10.2307/327317 https://doi.org/10.1080/01443410.2016.1149549"
    )
    assert _looks_like_merged_references(horwitz) is False
    # genuine merge (two years) still caught
    assert _looks_like_merged_references(_SCHMITT_WOLTER_MERGED) is True


@pytest.mark.asyncio
@respx.mock
async def test_book_form_rejects_openalex_article_via_bypass(tmp_path):
    """Self-review gap: the book-form article-type guard read item['type'],
    but OpenAlex candidates carried no type, so a same-exact-title journal
    article a year or two from a book (Bandura 1997 → a 1999 'Self-Efficacy:
    The Exercise of Control' article) could slip through the near-window
    bypass on the OpenAlex path. OpenAlex items now carry their type so the
    guard applies there too."""
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    from app.services.verifier import _search_openalex
    await init_db(db_path)

    entry = ReferenceEntry(
        raw_text="Bandura, A. (1997). Self-efficacy: The exercise of control. W. H. Freeman.",
        first_author_normalized="bandura", year=1997,
        title_normalized="selfefficacy the exercise of control",
    )
    openalex_payload = {"results": [{
        "title": "Self-Efficacy: The Exercise of Control",   # identical title
        "authorships": [{"author": {"display_name": "Albert Bandura"}}],
        "publication_year": 1999,                            # 2-year gap
        "doi": "https://doi.org/10.1/wrong",
        "type": "article",
        "host_venue": {"display_name": "Some Journal"},
    }]}
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json=openalex_payload)
    )
    result, _ = await _search_openalex(httpx.AsyncClient(), entry, db_path)
    assert result is None   # article rejected for a book-form entry


# ── Stage 2: LLM fix suggestions (opt-in) ─────────────────────────────────────

class _FakeContentBlock:
    def __init__(self, text): self.type = "text"; self.text = text
class _FakeResp:
    def __init__(self, text): self.content = [_FakeContentBlock(text)]
class _FakeMessages:
    def __init__(self, handler): self._handler = handler
    async def create(self, **kwargs): return self._handler(kwargs)
class _FakeAsyncAnthropic:
    """Minimal stand-in for anthropic.AsyncAnthropic — no network."""
    def __init__(self, handler): self.messages = _FakeMessages(handler)
    async def close(self): pass


def _citation(cid, kind="reference", issues=None, suggestion=None):
    c = {"id": cid, "kind": kind, "raw_text": f"Ref {cid}.", "issues": issues or []}
    if suggestion:
        c["suggestion"] = suggestion
    return c


@pytest.mark.asyncio
async def test_suggest_fixes_only_targets_fixable_reference_citations():
    """Only reference citations with a format/field issue are sent to the LLM;
    passes, not-found-only, in-text, and already-analysed citations are skipped."""
    from app.services.fix_suggester import suggest_fixes
    seen = []

    def handler(kwargs):
        seen.append(kwargs["messages"][0]["content"])
        return _FakeResp('{"corrected_reference": "FIXED", "explanation": "did x"}')

    citations = [
        _citation("r1", issues=[{"type": "format_violation", "reason": "R010"}]),
        _citation("r2", issues=[{"type": "field_mismatch", "reason": "Year mismatch",
                                 "expected": "2019", "actual": "2018"}]),
        _citation("r3", issues=[]),                                        # pass
        _citation("r4", issues=[{"type": "not_found", "reason": "gone"}]), # can't rewrite
        _citation("i5", kind="intext", issues=[{"type": "format_violation", "reason": "x"}]),
        _citation("r6", issues=[{"type": "format_violation", "reason": "y"}],
                  suggestion="already"),                                   # already analysed
    ]
    result = await suggest_fixes(citations, client=_FakeAsyncAnthropic(handler))
    assert set(result.keys()) == {"r1", "r2"}
    assert result["r1"]["suggestion"] == "FIXED"
    assert result["r2"]["suggestion_explanation"] == "did x"
    assert len(seen) == 2
    # the prompt carries the detected issue + expected/actual values
    assert "Year mismatch" in seen[1] and "2019" in seen[1] and "2018" in seen[1]


@pytest.mark.asyncio
async def test_suggest_fixes_skips_on_llm_error_without_failing_batch():
    """One citation's API failure or unparseable output must not drop the
    others — the report is still valid without a suggestion."""
    from app.services.fix_suggester import suggest_fixes

    def handler(kwargs):
        body = kwargs["messages"][0]["content"]
        if "r1" in body:
            raise RuntimeError("api down")
        if "r2" in body:
            return _FakeResp("not json")
        return _FakeResp('{"corrected_reference": "OK", "explanation": ""}')

    citations = [
        _citation("r1", issues=[{"type": "format_violation", "reason": "a"}]),
        _citation("r2", issues=[{"type": "format_violation", "reason": "b"}]),
        _citation("r3", issues=[{"type": "format_violation", "reason": "c"}]),
    ]
    result = await suggest_fixes(citations, client=_FakeAsyncAnthropic(handler))
    assert set(result.keys()) == {"r3"}   # r1 (error) and r2 (bad json) skipped


@pytest.mark.asyncio
async def test_suggest_fixes_empty_when_nothing_fixable():
    """No fixable citations → no client call at all."""
    from app.services.fix_suggester import suggest_fixes
    calls = []
    citations = [_citation("r1", issues=[]), _citation("i2", kind="intext")]
    result = await suggest_fixes(
        citations,
        client=_FakeAsyncAnthropic(lambda k: calls.append(k) or _FakeResp("{}")),
    )
    assert result == {} and calls == []


# ── RAG PR1: APA rules corpus + index builder ────────────────────────────────

def test_apa_corpus_loads_and_is_valid():
    """The committed corpus must load, have unique chunk_ids, and carry every
    required field — a malformed chunk would otherwise only surface when the
    index is rebuilt (a manual step that may be days later)."""
    from app.scripts.build_rules_index import load_corpus, DEFAULT_CORPUS_DIR, REQUIRED_FIELDS
    chunks = load_corpus(DEFAULT_CORPUS_DIR)
    assert len(chunks) >= 30
    ids = [c["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids)), "duplicate chunk_id"
    for c in chunks:
        for field in REQUIRED_FIELDS:
            assert c.get(field), f"{c.get('chunk_id')} missing {field}"
        assert c["source_url"].startswith("https://"), c["chunk_id"]


def test_apa_corpus_covers_every_text_checkable_rule():
    """Every rule the self-validation loop can check must have at least one
    guidance chunk backing it, otherwise a retry has no grounding to work from.
    R006 (hanging indent) is excluded: it is a layout rule that cannot be
    judged from a plain-text suggestion, so the loop never validates it."""
    from app.scripts.build_rules_index import load_corpus, DEFAULT_CORPUS_DIR
    covered = {r for c in load_corpus(DEFAULT_CORPUS_DIR) for r in c["related_rules"]}
    expected = {f"R{n:03d}" for n in list(range(1, 6)) + list(range(7, 21))} - {"R004"}
    assert expected <= covered, f"rules with no guidance chunk: {sorted(expected - covered)}"


def test_corpus_hash_is_deterministic_and_content_sensitive():
    """corpus_sha256 gates index rebuilds: it must be stable across runs (or
    every start-up fails) yet change when embedded text changes (or a stale
    index silently serves outdated guidance)."""
    from app.scripts.build_rules_index import load_corpus, corpus_sha256, DEFAULT_CORPUS_DIR
    chunks = load_corpus(DEFAULT_CORPUS_DIR)
    assert corpus_sha256(chunks) == corpus_sha256(load_corpus(DEFAULT_CORPUS_DIR))

    mutated = [dict(c) for c in chunks]
    mutated[0]["text"] = mutated[0]["text"] + " extra guidance."
    assert corpus_sha256(mutated) != corpus_sha256(chunks)


def test_corpus_loader_rejects_duplicate_and_incomplete_chunks(tmp_path):
    """Fail loudly at build time rather than shipping a broken index."""
    from app.scripts.build_rules_index import load_corpus
    (tmp_path / "a.yaml").write_text(
        "- {chunk_id: x, category: c, title: t, text: body, source_url: 'https://e.org'}\n"
        "- {chunk_id: x, category: c, title: t2, text: body2, source_url: 'https://e.org'}\n"
    )
    with pytest.raises(ValueError, match="duplicate chunk_id"):
        load_corpus(tmp_path)

    (tmp_path / "a.yaml").write_text(
        "- {chunk_id: y, category: c, title: t, text: body}\n"   # no source_url
    )
    with pytest.raises(ValueError, match="missing required field"):
        load_corpus(tmp_path)


def test_embed_text_includes_category_and_title():
    """The embedded string carries the citation *type*, so a query naming a
    type ('encyclopedia entry') can match a chunk that shares no distinctive
    body vocabulary with it."""
    from app.scripts.build_rules_index import embed_text
    chunk = {"category": "reference-work", "title": "Encyclopedia entries",
             "text": "Entries are cited with the entry author..."}
    embedded = embed_text(chunk)
    assert "reference-work" in embedded and "Encyclopedia entries" in embedded
    assert "entry author" in embedded


def test_write_index_roundtrip_with_fake_vectors(tmp_path):
    """Build a real sqlite-vec index from fake vectors (no API) and query it
    back — proves the schema, the vec0 table, and index_meta all work, and
    that a KNN query returns the nearest chunk."""
    from app.scripts.build_rules_index import write_index
    from app.services.vectors import open_rules_db      # PR2 helper, added below
    chunks = [
        {"chunk_id": "a", "category": "journal-article", "title": "A", "text": "aaa",
         "source_url": "https://e.org/a", "related_rules": ["R010"]},
        {"chunk_id": "b", "category": "chapter", "title": "B", "text": "bbb",
         "source_url": "https://e.org/b", "related_rules": []},
    ]
    vectors = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
    out = tmp_path / "idx.db"
    write_index(out, chunks, vectors,
                {"embedding_provider": "openai", "embedding_model": "m",
                 "embedding_dim": "4", "normalized": "true",
                 "corpus_sha256": "deadbeef", "built_at": "now",
                 "sqlite_vec_version": "v0.1.9"})

    db = open_rules_db(str(out))
    meta = dict(db.execute("SELECT key, value FROM index_meta").fetchall())
    assert meta["embedding_dim"] == "4" and meta["corpus_sha256"] == "deadbeef"

    import struct
    hit = db.execute(
        """SELECT c.chunk_id FROM rule_vectors v JOIN rule_chunks c ON c.rowid = v.rowid
           WHERE v.embedding MATCH ? AND k = 1""",
        (struct.pack("4f", 1.0, 0.0, 0.0, 0.0),),
    ).fetchone()
    assert hit[0] == "a"
    db.close()


@pytest.mark.asyncio
async def test_noop_fix_suggestion_is_dropped():
    """Bug (observed against the live API on Wang & Sun 2020): Claude replied
    "no correction was needed" yet still returned corrected_reference set to
    the original string. The UI then rendered an "AI fix suggestion" that was
    character-for-character what the student wrote. A suggestion identical to
    the input is not a suggestion — drop it."""
    from app.services.fix_suggester import suggest_fixes
    raw = ("Wang, C., & Sun, T. (2020). Relationship between self-efficacy and "
           "language proficiency: A meta-analysis. System, 95, 102366.")

    def handler(kwargs):
        return _FakeResp(json.dumps({
            "corrected_reference": raw,
            "explanation": "The reference is already correct; no changes were made.",
        }))

    citations = [{"id": "c1", "kind": "reference", "raw_text": raw,
                  "issues": [{"type": "format_violation", "reason": "Missing volume/pages"}]}]
    assert await suggest_fixes(citations, client=_FakeAsyncAnthropic(handler)) == {}


@pytest.mark.asyncio
async def test_whitespace_only_rewrite_is_dropped():
    """Re-wrapping or collapsing spaces isn't a fix either, and the NBSP Word
    inserts would otherwise make an identical string compare as different."""
    from app.services.fix_suggester import suggest_fixes
    raw = "Meichenbaum, D. (1977).\xa0Cognitive-behavior modification. Plenum Press."

    def handler(kwargs):
        return _FakeResp(json.dumps({
            "corrected_reference":
                "Meichenbaum, D. (1977).  Cognitive-behavior modification.  Plenum Press.",
            "explanation": "Tidied spacing.",
        }))

    citations = [{"id": "c1", "kind": "reference", "raw_text": raw,
                  "issues": [{"type": "format_violation", "reason": "Title should be italic"}]}]
    assert await suggest_fixes(citations, client=_FakeAsyncAnthropic(handler)) == {}


@pytest.mark.asyncio
async def test_capitalisation_only_rewrite_survives_the_noop_filter():
    """Guard the fix from over-reaching: recapitalising a journal name (R018)
    is a genuine correction, so the comparison must stay case-sensitive."""
    from app.services.fix_suggester import suggest_fixes
    raw = "Smith, J. (2020). A study. journal of testing, 5(2), 1-10."
    fixed = "Smith, J. (2020). A study. Journal of Testing, 5(2), 1-10."

    def handler(kwargs):
        return _FakeResp(json.dumps({
            "corrected_reference": fixed, "explanation": "Capitalised the journal name.",
        }))

    citations = [{"id": "c1", "kind": "reference", "raw_text": raw,
                  "issues": [{"type": "format_violation", "reason": "Journal name capitalisation"}]}]
    out = await suggest_fixes(citations, client=_FakeAsyncAnthropic(handler))
    assert out["c1"]["suggestion"] == fixed


# ── R021: chapter cited with no page range ───────────────────────────────────

def _para(raw: str) -> ReferenceParagraph:
    return ReferenceParagraph(raw_text=raw, runs=[], has_hanging_indent=True)


def test_r021_flags_encyclopedia_entry_with_no_page_range():
    """Bug (Grimm 2010): an encyclopedia entry with editors, book title and a
    DOI but no page range passed as "APA format correct". R019 could not catch
    it — R019 only fires when the authoritative record supplies a page range,
    and Crossref returns no `page` field for 10.1002/9781444316568.wiem02057,
    which is typical for encyclopedia entries."""
    from app.rules.apa7 import check_chapter_page_range
    issue = check_chapter_page_range(_para(
        "Grimm, P. (2010). Social desirability bias. In J. Sheth & N. Malhotra "
        "(Eds.), Wiley international encyclopedia of marketing. Wiley. "
        "https://doi.org/10.1002/9781444316568.wiem02057"
    ))
    assert issue is not None and issue.rule_id == "R021"


def test_r021_ignores_volume_prefixed_page_ranges():
    """False positive found while evaluating the rule against References
    check06: '(Vol. 2, pp. 27-44)' does carry a page range, but _PAGE_GROUP_RE
    requires 'pp.' to follow the opening paren directly, so the first draft
    reported it as missing."""
    from app.rules.apa7 import check_chapter_page_range
    assert check_chapter_page_range(_para(
        "Sarason, I. G. (1975). Anxiety and self-preoccupation. In I. G. Sarason "
        "& C. D. Spielberger (Eds.), Stress and anxiety (Vol. 2, pp. 27-44). "
        "Hemisphere."
    )) is None


def test_r021_leaves_malformed_page_ranges_to_r015():
    """A bare range (', 441-461.') is page information, badly formatted. R021
    must stay quiet so the user isn't told the pages are absent when they are
    merely mispunctuated."""
    from app.rules.apa7 import check_chapter_page_range
    assert check_chapter_page_range(_para(
        "Corder, S., & Meyerhoff, M. (2007). Communities of practice. In "
        "H. Kotthoff & H. Spencer-Oatey (Eds.), Handbook of intercultural "
        "communication, 441-461. Walter de Gruyter."
    )) is None


def test_r021_ignores_well_formed_chapters_and_non_chapters():
    from app.rules.apa7 import check_chapter_page_range
    good_chapter = _para(
        "Zimmerman, B. J., & Cleary, T. J. (2006). Adolescents' development. In "
        "F. Pajares & T. Urdan (Eds.), Self-efficacy beliefs of adolescents "
        "(pp. 45-69). Information Age Publishing."
    )
    journal = _para(
        "Durrant, P., & Schmitt, N. (2009). To what extent do writers use "
        "collocations? IRAL, 47(2), 157-177."
    )
    assert check_chapter_page_range(good_chapter) is None
    assert check_chapter_page_range(journal) is None


def test_r021_detail_distinguishes_missing_locator():
    """A reference with neither pages nor a DOI/URL is incomplete under either
    reading, so it gets firmer advice than one that at least has a locator."""
    from app.rules.apa7 import check_chapter_page_range
    with_doi = check_chapter_page_range(_para(
        "Grimm, P. (2010). Social desirability bias. In J. Sheth & N. Malhotra "
        "(Eds.), Wiley international encyclopedia of marketing. Wiley. "
        "https://doi.org/10.1002/9781444316568.wiem02057"))
    without = check_chapter_page_range(_para(
        "Grimm, P. (2010). Social desirability bias. In J. Sheth & N. Malhotra "
        "(Eds.), Wiley international encyclopedia of marketing. Wiley."))
    assert "already correct" in with_doi.detail
    assert "nor a DOI/URL" in without.detail


def test_r015_flags_page_range_written_without_parentheses():
    """Gap found in References check03: three APA 6-style references put the
    chapter page range after a comma ('Handbook of intercultural
    communication, 441-461.'). R015 only recognised the parenthesised form
    '(351-381)', so all three passed with no issue reported at all."""
    from app.rules.apa7 import check_chapter_page_format
    for raw in (
        "Corder, S., & Meyerhoff, M. (2007). Communities of practice. In "
        "H. Kotthoff & H. Spencer-Oatey (Eds.), Handbook of intercultural "
        "communication, 441-461. Walter de Gruyter.",
        "Eckert, P. (2009). Communities of practice. In J. L. Mey (Ed.), "
        "Concise encyclopedia of pragmatics (2nd ed.), 109-112. Elsevier.",
        "Labov, W. (1989). The exact description. In R. W. Fasold & "
        "D. Schiffrin (Eds.), Language change and variation, 1-57. Benjamins.",
    ):
        issue = check_chapter_page_format(_para(raw))
        assert issue is not None and issue.rule_id == "R015", raw[:40]


def test_r015_accepts_pp_not_directly_after_the_paren():
    """'(Vol. 2, pp. 27-44)' is correct APA. The old gate required 'pp.' to
    follow the opening paren directly, which made this depend on the bare-range
    pattern happening not to match."""
    from app.rules.apa7 import check_chapter_page_format
    assert check_chapter_page_format(_para(
        "Sarason, I. G. (1975). Anxiety and self-preoccupation. In I. G. Sarason "
        "& C. D. Spielberger (Eds.), Stress and anxiety (Vol. 2, pp. 27-44). "
        "Hemisphere."
    )) is None


def test_doi_digits_are_not_mistaken_for_a_page_range():
    """A Springer-style DOI suffix ('10.1007/978-3-319-12345-6_7') contains
    digit-hyphen-digit runs. Without stripping locators first, R021 read that
    as 'pages are present' and stayed silent on chapters carrying such DOIs —
    the opposite of the rule's purpose."""
    from app.rules.apa7 import check_chapter_page_range
    issue = check_chapter_page_range(_para(
        "Smith, J. (2020). A chapter. In A. Editor (Ed.), Some handbook. "
        "Springer. https://doi.org/10.1007/978-3-319-12345-6_7"
    ))
    assert issue is not None and issue.rule_id == "R021"


# ── R022: APA 6 publisher location ───────────────────────────────────────────

def test_r022_flags_publisher_location():
    """APA 7 dropped the publisher's location. Gap found in References
    check03, where five references still carried it and nothing was
    reported."""
    from app.rules.apa7 import check_publisher_location
    for raw, expected_actual in (
        ("Corder, S. (2007). Communities of practice. In H. Kotthoff (Ed.), "
         "Handbook of intercultural communication (pp. 441-461). "
         "Berlin, Germany: Walter de Gruyter.", "Berlin, Germany"),
        ("Labov, W. (1989). The exact description of the speech community: "
         "Short a in Philadelphia. In R. W. Fasold (Ed.), Language change "
         "(pp. 1-57). Amsterdam: John Benjamins.", "Amsterdam"),
        ("Eckert, P. (2018). Meaning and linguistic variation: The third wave "
         "in sociolinguistics. Cambridge: Cambridge University Press.",
         "Cambridge"),
    ):
        issue = check_publisher_location(_para(raw))
        assert issue is not None and issue.rule_id == "R022", raw[:40]
        assert issue.actual.startswith(expected_actual)


def test_r022_accepts_apa7_publisher_alone():
    from app.rules.apa7 import check_publisher_location
    for raw in (
        "Zimmerman, B. J. (2006). Agency. In F. Pajares (Ed.), Self-efficacy "
        "beliefs of adolescents (pp. 45-69). Information Age Publishing.",
        "Bezuidenhout, J. (2020). A glossary of survey research methods. "
        "Sage Publications, Inc.",
        "Wenger-Trayner, E. (2020). Learning in landscapes of practice. Routledge.",
    ):
        assert check_publisher_location(_para(raw)) is None, raw[:40]


def test_r022_ignores_a_colon_inside_a_title():
    """A subtitle colon is always followed by more elements, so anchoring the
    pattern to the final element keeps titles out of it."""
    from app.rules.apa7 import check_publisher_location
    assert check_publisher_location(_para(
        "Labov, W. (1989). The exact description of the speech community: "
        "Short a in Philadelphia. In R. W. Fasold & D. Schiffrin (Eds.), "
        "Language change and variation (pp. 1-57). John Benjamins."
    )) is None


def test_r022_ignores_journal_articles():
    from app.rules.apa7 import check_publisher_location
    assert check_publisher_location(_para(
        "Durrant, P., & Schmitt, N. (2009). To what extent do writers use "
        "collocations? IRAL, 47(2), 157-177."
    )) is None
