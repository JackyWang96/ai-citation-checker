import pytest
import respx
import httpx
from app.services.verifier import verify_reference, VerifyResult
from app.services.citation_extractor import ReferenceEntry

CROSSREF_URL = "https://api.crossref.org/works"
OPENALEX_URL = "https://api.openalex.org/works"

def _entry(title="AI in education", author="smith", year=2020, doi=None) -> ReferenceEntry:
    return ReferenceEntry(
        raw_text=f"{author.title()}, J. ({year}). {title}. Journal, 1(1).",
        first_author_normalized=author,
        year=year,
        title_normalized=title.lower(),
        doi=doi,
    )

def _crossref_hit(title="AI in education", author="Smith", year=2020):
    return {
        "status": "ok",
        "message": {
            "items": [{
                "title": [title],
                "author": [{"family": author, "given": "John"}],
                "published": {"date-parts": [[year]]},
                "container-title": ["Journal of Educational Psychology"],
                "volume": "112", "issue": "3", "page": "45-62",
                "DOI": "10.1037/edu0000412",
                "score": 95.0,
            }]
        }
    }

@pytest.mark.asyncio
@respx.mock
async def test_exact_match_returns_green(tmp_path):
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    await init_db(db_path)

    respx.get(CROSSREF_URL).mock(
        return_value=httpx.Response(200, json=_crossref_hit())
    )
    result = await verify_reference(_entry(), db_path)
    assert result.found is True
    assert result.exact_match is True

@pytest.mark.asyncio
@respx.mock
async def test_no_match_returns_not_found(tmp_path):
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    await init_db(db_path)

    respx.get(CROSSREF_URL).mock(
        return_value=httpx.Response(200, json={"status":"ok","message":{"items":[]}})
    )
    respx.get(OPENALEX_URL).mock(
        return_value=httpx.Response(200, json={"results":[]})
    )
    result = await verify_reference(_entry(title="Completely fake paper"), db_path)
    assert result.found is False

@pytest.mark.asyncio
@respx.mock
async def test_ambiguous_flagged(tmp_path):
    db_path = str(tmp_path / "t.db")
    from app.storage.db import init_db
    await init_db(db_path)

    two_hits = {
        "status": "ok",
        "message": {
            "items": [
                {"title":["AI in education"],"author":[{"family":"Smith","given":"J"}],
                 "published":{"date-parts":[[2020]]},"container-title":["J Ed"],
                 "DOI":"10.1/a","score":99},
                {"title":["AI in learning"],"author":[{"family":"Smith","given":"J"}],
                 "published":{"date-parts":[[2020]]},"container-title":["J Ed"],
                 "DOI":"10.1/b","score":85},
            ]
        }
    }
    respx.get(CROSSREF_URL).mock(return_value=httpx.Response(200, json=two_hits))
    result = await verify_reference(_entry(), db_path)
    assert result.ambiguous is True
    assert len(result.other_titles) >= 1
