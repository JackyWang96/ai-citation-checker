import pytest
from app.rules.apa7 import check_author_format, check_year_parens, check_doi_format, check_author_separator
from app.services.docx_parser import ReferenceParagraph

def _para(text: str, runs=None, hanging=True) -> ReferenceParagraph:
    if runs is None:
        runs = [(text, False)]
    return ReferenceParagraph(raw_text=text, runs=runs, has_hanging_indent=hanging)

def test_r001_good_author_passes():
    assert check_author_format(_para("Smith, J. (2020). Title.")) is None

def test_r001_bad_author_flagged():
    issue = check_author_format(_para("Smith J (2020). Title."))
    assert issue is not None
    assert issue.rule_id == "R001"
    assert issue.severity == "yellow"

def test_r002_year_parens_good():
    assert check_year_parens(_para("Smith, J. (2020). Title.")) is None

def test_r002_year_parens_bad():
    issue = check_year_parens(_para("Smith, J. 2020. Title."))
    assert issue is not None
    assert issue.rule_id == "R002"

def test_r005_doi_url_good():
    assert check_doi_format(_para("...https://doi.org/10.1000/xyz")) is None

def test_r005_doi_bad_format():
    issue = check_doi_format(_para("...doi:10.1000/xyz"))
    assert issue is not None
    assert issue.rule_id == "R005"

def test_r005_no_doi_passes():
    assert check_doi_format(_para("Smith, J. (2020). Title. Journal, 1(1).")) is None

def test_r007_ampersand_good():
    assert check_author_separator(_para("Smith, J., & Jones, A. (2020). T.")) is None

def test_r007_ampersand_bad():
    issue = check_author_separator(_para("Smith, J. and Jones, A. (2020). T."))
    assert issue is not None
    assert issue.rule_id == "R007"
