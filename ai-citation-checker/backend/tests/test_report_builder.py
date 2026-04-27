import pytest
from app.services.report_builder import build_report
from app.services.citation_extractor import IntextCitation, ReferenceEntry
from app.services.verifier import VerifyResult
from app.services.docx_parser import ReferenceParagraph

def _intext(author="Smith", year=2020, start=0) -> IntextCitation:
    raw = f"({author}, {year})"
    return IntextCitation(raw_text=raw, author=author, year=year,
                          char_start=start, char_end=start+len(raw))

def _entry(author="smith", year=2020, title="ai in education") -> ReferenceEntry:
    return ReferenceEntry(
        raw_text=f"{author.title()}, J. ({year}). {title}.",
        first_author_normalized=author, year=year, title_normalized=title,
    )

def _para(text: str) -> ReferenceParagraph:
    # Use italic=True so R003 (journal italic check) passes
    return ReferenceParagraph(raw_text=text, runs=[(text, True)], has_hanging_indent=True)

def _result(found=True, exact=True) -> VerifyResult:
    canonical = {"title":["AI in education"],"author":[{"family":"Smith","given":"J"}],
                 "published":{"date-parts":[[2020]]},"container-title":["Journal of Ed"],
                 "volume":"1","issue":"1","page":"1-10","DOI":"10.1/a"}
    return VerifyResult(found=found, exact_match=exact, canonical=canonical if found else None)

def test_all_green_report():
    report = build_report(
        report_id="r1", filename="essay.docx", full_text="(Smith, 2020).",
        intext_citations=[_intext()],
        reference_entries=[_entry()],
        reference_paragraphs=[_para("Smith, J. (2020). AI. Journal, 1(1).")],
        verify_results=[_result()],
    )
    assert report.summary["pass"] == 2   # 1 intext + 1 reference
    assert report.summary["error"] == 0

def test_orphan_intext_flagged():
    report = build_report(
        report_id="r1", filename="essay.docx", full_text="(Ghost, 2099).",
        intext_citations=[_intext("Ghost", 2099)],
        reference_entries=[_entry()],
        reference_paragraphs=[_para("Smith, J. (2020). AI. Journal.")],
        verify_results=[_result()],
    )
    orphan_citations = [c for c in report.citations
                        if any(i.category == "orphan" for i in c.issues)]
    assert len(orphan_citations) == 1

def test_not_found_reference_is_red():
    report = build_report(
        report_id="r1", filename="essay.docx", full_text="(Fake, 2099).",
        intext_citations=[_intext("Fake", 2099)],
        reference_entries=[_entry("fake", 2099, "nonexistent paper")],
        reference_paragraphs=[_para("Fake, A. (2099). Nonexistent paper. Fake Journal.")],
        verify_results=[_result(found=False)],
    )
    red = [c for c in report.citations if c.status == "error"]
    assert len(red) >= 1
