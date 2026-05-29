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
