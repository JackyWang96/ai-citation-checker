"""Run once to generate test .docx fixture files."""
from docx import Document
from pathlib import Path

OUT = Path(__file__).parent

def make_clean():
    doc = Document()
    doc.add_paragraph(
        "Artificial intelligence is transforming education (Smith, 2020). "
        "Recent studies support this claim (Jones & Lee, 2019)."
    )
    doc.add_heading("References", level=1)
    ref1 = doc.add_paragraph()
    ref1.add_run("Smith, J. (2020). ")
    r = ref1.add_run("AI in education.")
    r.italic = True
    ref1.add_run(" Journal of Educational Psychology, 112(3), 45-62. https://doi.org/10.1037/edu0000412")
    ref2 = doc.add_paragraph()
    ref2.add_run("Jones, A., & Lee, B. (2019). ")
    r2 = ref2.add_run("Learning with machines.")
    r2.italic = True
    ref2.add_run(" Educational Research, 5(1), 1-20. https://doi.org/10.1000/xyz123")
    doc.save(OUT / "clean.docx")

def make_fabricated():
    doc = Document()
    doc.add_paragraph("AI hallucinated this reference (Fake, 2099).")
    doc.add_heading("References", level=1)
    doc.add_paragraph(
        "Fake, A. (2099). This paper does not exist. "
        "Journal of Nonexistent Studies, 1(1), 1-10."
    )
    doc.save(OUT / "fabricated.docx")

def make_format_errors():
    doc = Document()
    doc.add_paragraph("Some claim (Smith 2020).")  # missing comma
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smith J (2020). ")  # bad author format
    ref.add_run("AI in education.")  # journal not italic
    ref.add_run(" Journal of Educational Psychology, 112(3), 45-62. doi:10.1037/edu0000412")
    doc.save(OUT / "format_errors.docx")

def make_metadata_mismatch():
    doc = Document()
    doc.add_paragraph("(Smyth, 2019).")
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smyth, J. (2019). ")
    r = ref.add_run("AI in education.")
    r.italic = True
    ref.add_run(" Jornal of Educational Psychology, 12(3), 45-62.")
    doc.save(OUT / "metadata_mismatch.docx")

def make_orphan_intext():
    doc = Document()
    doc.add_paragraph("This claim (Ghost, 2021) has no reference.")
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smith, J. (2020). ")
    r = ref.add_run("AI in education.")
    r.italic = True
    ref.add_run(" Journal of Educational Psychology, 112(3), 45-62.")
    doc.save(OUT / "orphan_intext.docx")

def make_ambiguous_author():
    doc = Document()
    doc.add_paragraph("(Smith, 2020).")
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smith, J. (2020). ")
    r = ref.add_run("AI in education.")
    r.italic = True
    ref.add_run(" Journal of Educational Psychology, 112(3), 45-62.")
    doc.save(OUT / "ambiguous_author.docx")

def make_missing_subtitle():
    doc = Document()
    doc.add_paragraph("(Smith, 2020).")
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smith, J. (2020). ")
    r = ref.add_run("AI in education.")  # missing ": A systematic review"
    r.italic = True
    ref.add_run(" Journal of Educational Psychology, 112(3), 45-62.")
    doc.save(OUT / "missing_subtitle.docx")

def make_mixed():
    doc = Document()
    doc.add_paragraph("Real claim (Smith, 2020). Fake claim (Ghost, 2099). Bad format (jones 2018).")
    doc.add_heading("References", level=1)
    ref1 = doc.add_paragraph()
    ref1.add_run("Smith, J. (2020). ")
    r = ref1.add_run("AI in education.")
    r.italic = True
    ref1.add_run(" Journal of Educational Psychology, 112(3), 45-62.")
    doc.add_paragraph("Ghost, A. (2099). Hallucinated paper. Fake Journal, 1(1), 1-5.")
    doc.save(OUT / "mixed.docx")

if __name__ == "__main__":
    make_clean()
    make_fabricated()
    make_format_errors()
    make_metadata_mismatch()
    make_orphan_intext()
    make_ambiguous_author()
    make_missing_subtitle()
    make_mixed()
    print("Fixtures generated.")
