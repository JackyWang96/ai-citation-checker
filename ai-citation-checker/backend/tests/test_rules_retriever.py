"""Offline tests for the APA rules retrieval layer.

No network and no API key: query vectors are hand-written and the index is a
tiny fake built by the real `write_index`, so the sqlite-vec plumbing is
genuinely exercised while CI stays deterministic. Recall quality is measured
separately by `python -m app.scripts.eval_retrieval`, which does hit OpenAI.
"""
from __future__ import annotations

import textwrap

import pytest

import app.config as cfg
from app.scripts.build_rules_index import write_index
from app.services import rules_retriever as rr
from app.services.rules_corpus import corpus_sha256, load_corpus

DIM = 4

# Three chunks whose vectors point along different axes, so "nearest" is
# unambiguous and the assertions below don't depend on floating-point luck.
_VECTORS = {
    "apa7-journal-basic-shape": [1.0, 0.0, 0.0, 0.0],
    "apa7-whole-book": [0.0, 1.0, 0.0, 0.0],
    "apa7-author-name-format": [0.0, 0.0, 1.0, 0.0],
}

_CORPUS_YAML = textwrap.dedent(
    """
    - chunk_id: apa7-journal-basic-shape
      category: journal-article
      title: Basic shape of a journal article reference
      text: Author, A. A. (Year). Title of article. Journal Name, Volume(Issue), pages.
      source_url: https://apastyle.apa.org/journal-article
    - chunk_id: apa7-whole-book
      category: book
      title: Whole book reference
      text: Author, A. A. (Year). Title of work. Publisher.
      source_url: https://apastyle.apa.org/book
    - chunk_id: apa7-author-name-format
      category: authors
      title: Author names are surname first, then initials
      text: Invert every author name, giving the surname then the initials.
      source_url: https://apastyle.apa.org/author-names
    """
).strip()


@pytest.fixture
def fake_index(tmp_path, monkeypatch):
    """A real (tiny) sqlite-vec index whose fingerprint matches the config."""
    corpus_dir = tmp_path / "apa_rules"
    corpus_dir.mkdir()
    (corpus_dir / "rules.yaml").write_text(_CORPUS_YAML, encoding="utf-8")

    chunks = load_corpus(corpus_dir)
    db_path = tmp_path / "apa_rules.db"
    write_index(
        db_path,
        chunks,
        [_VECTORS[c["chunk_id"]] for c in chunks],
        {
            "embedding_provider": "openai",
            "embedding_model": "text-embedding-3-small",
            "embedding_dim": str(DIM),
            "normalized": "true",
            "corpus_sha256": corpus_sha256(chunks),
            "built_at": "2026-08-17T00:00:00+00:00",
            "sqlite_vec_version": "v0.1.9",
        },
    )

    monkeypatch.setattr(cfg, "RULES_DB_PATH", str(db_path))
    monkeypatch.setattr(cfg, "RULES_CORPUS_DIR", str(corpus_dir))
    monkeypatch.setattr(cfg, "EMBEDDING_DIM", DIM)
    monkeypatch.setattr(cfg, "RAG_ENABLED", True)
    monkeypatch.setattr(rr, "_db", None)   # drop the process-wide handle
    yield db_path
    monkeypatch.setattr(rr, "_db", None)


# -- type_hints ---------------------------------------------------------------

@pytest.mark.parametrize("raw_text,expected", [
    ("MacWhinney, B. (2008). A unified model. In P. Robinson & N. C. Ellis "
     "(Eds.), Handbook of cognitive linguistics (pp. 351-381). Routledge.",
     "edited book chapter"),
    ("Durrant, P., & Schmitt, N. (2009). To what extent do writers use "
     "collocations? IRAL, 47(2), 157-177.",
     "journal article"),
    ("Wang, C., & Sun, T. (2020). Relationship between self-efficacy and "
     "language proficiency. System, 95, 102366.",
     "journal article with an article number"),
    ("R Core Team. (2023). R: A language for statistical computing "
     "[Computer software]. R Foundation.",
     "software"),
    ("Meichenbaum, D. (1977). Cognitive-behavior modification. Plenum Press.",
     "book"),
    ("IELTS Partners. (n.d.). Test taker performance. IELTS.",
     "undated source"),
    ("World Health Organization. (2021). Mental health report. "
     "https://who.int/reports/mh",
     "web page"),
])
def test_type_hints_are_structural(raw_text, expected):
    """Hints come from the reference's shape, never from its words."""
    assert rr.type_hints(raw_text) == expected


def test_article_number_form_is_not_mistaken_for_a_book():
    """'System, 95, 102366' has no page range, so _journal_name can't see it.
    Without the article-number branch it fell through to 'book' -- the wrong
    hint for exactly the references R020 fires on."""
    raw = ("Wang, C., & Sun, T. (2020). Relationship between self-efficacy "
           "and language proficiency. System, 95, 102366.")
    assert rr.type_hints(raw) == "journal article with an article number"


# -- build_query --------------------------------------------------------------

def test_query_contains_no_proper_nouns_from_the_reference():
    """The PR 1 recall evaluation's central finding: author, journal, and
    title words dominate the embedding and bury the actual problem, dropping
    top-3 recall from 6/6 to 4/6. The query must carry none of them."""
    citation = {
        "raw_text": ("Van Vu, D., & Peters, E. (2022). Incidental learning of "
                     "collocations from meaningful input. Studies in Second "
                     "Language Acquisition, 44(3), 685-707."),
        "issues": [{"type": "field_mismatch", "reason": "Author name mismatch",
                    "expected": "Vu", "actual": "Van Vu"}],
    }
    query = rr.build_query(citation)
    for proper_noun in ("Van Vu", "Peters", "Studies in Second Language",
                        "Incidental"):
        assert proper_noun not in query, f"{proper_noun!r} leaked into the query"
    assert "Author name mismatch" in query
    assert "journal article" in query


def test_query_ignores_issues_a_rewrite_cannot_fix():
    """'Reference not found' can't be fixed by reformatting, so it must not
    steer retrieval toward whatever chunk mentions missing records."""
    citation = {
        "raw_text": "Meichenbaum, D. (1977). Cognitive-behavior modification. Plenum Press.",
        "issues": [
            {"type": "not_found", "reason": "Reference not found"},
            {"type": "format_violation", "reason": "Book title should be in italics"},
        ],
    }
    query = rr.build_query(citation)
    assert "not found" not in query.lower()
    assert "Book title should be in italics" in query


def test_query_survives_a_citation_with_no_usable_reason():
    citation = {"raw_text": "Anon. (2020). Something. Publisher.", "issues": []}
    assert rr.build_query(citation)   # non-empty, no exception


# -- verify_rules_index -------------------------------------------------------

def test_verify_accepts_a_matching_index(fake_index):
    rr.verify_rules_index(rr.open_rules_db(str(fake_index), read_only=True),
                          cfg.RULES_CORPUS_DIR)


@pytest.mark.parametrize("attr,value", [
    ("EMBEDDING_MODEL", "text-embedding-3-large"),
    ("EMBEDDING_PROVIDER", "voyage"),
    ("EMBEDDING_DIM", 1536),
    ("EMBEDDING_NORMALIZED", False),
])
def test_verify_rejects_an_incompatible_index(fake_index, monkeypatch, attr, value):
    """Vectors from a different provider/model/normalisation are not
    comparable. `normalized` is the dangerous case: same dimensions, no error,
    silently wrong ranking -- so it must be a hard failure, not a warning."""
    monkeypatch.setattr(cfg, attr, value)
    db = rr.open_rules_db(str(fake_index), read_only=True)
    with pytest.raises(RuntimeError, match="incompatible"):
        rr.verify_rules_index(db, cfg.RULES_CORPUS_DIR)


def test_verify_rejects_a_corpus_edited_without_a_rebuild(fake_index, tmp_path):
    """Editing the YAML without re-running build_rules_index would otherwise
    serve stale rule text under a fresh-looking index."""
    corpus = tmp_path / "apa_rules" / "rules.yaml"
    corpus.write_text(_CORPUS_YAML.replace("Publisher.", "Publisher, City."),
                      encoding="utf-8")
    db = rr.open_rules_db(str(fake_index), read_only=True)
    with pytest.raises(RuntimeError, match="corpus_sha256"):
        rr.verify_rules_index(db, cfg.RULES_CORPUS_DIR)


def test_verify_error_names_the_build_for_diagnosis(fake_index, monkeypatch):
    monkeypatch.setattr(cfg, "EMBEDDING_MODEL", "some-other-model")
    db = rr.open_rules_db(str(fake_index), read_only=True)
    with pytest.raises(RuntimeError) as exc:
        rr.verify_rules_index(db, cfg.RULES_CORPUS_DIR)
    assert "2026-08-17" in str(exc.value) and "v0.1.9" in str(exc.value)


# -- search -------------------------------------------------------------------

def test_search_vector_ranks_by_distance_and_returns_full_chunks(fake_index):
    hits = rr.search_vector(rr.open_index(), [0.0, 1.0, 0.0, 0.0], 3)
    assert hits[0].chunk_id == "apa7-whole-book"
    assert hits[0].source_url == "https://apastyle.apa.org/book"
    assert hits[0].title == "Whole book reference"
    assert [h.distance for h in hits] == sorted(h.distance for h in hits)


def test_search_vector_respects_top_k(fake_index):
    assert len(rr.search_vector(rr.open_index(), [1.0, 0.0, 0.0, 0.0], 2)) == 2


async def test_search_fails_open_when_embedding_breaks(fake_index, monkeypatch):
    """Retrieval is an enhancement, not a dependency: an embedding outage must
    degrade the suggestion to its non-RAG quality, never fail the request."""
    async def boom(*a, **kw):
        raise RuntimeError("embedding provider is down")
    monkeypatch.setattr(rr, "embed_query", boom)
    citation = {"raw_text": "Meichenbaum, D. (1977). Title. Publisher.",
                "issues": [{"type": "format_violation", "reason": "Title should be italic"}]}
    assert await rr.search(citation) == []


async def test_search_fails_open_when_the_index_is_unusable(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "RAG_ENABLED", True)
    monkeypatch.setattr(cfg, "RULES_DB_PATH", str(tmp_path / "missing.db"))
    monkeypatch.setattr(rr, "_db", None)

    async def fake_embed(*a, **kw):
        return [1.0, 0.0, 0.0, 0.0]
    monkeypatch.setattr(rr, "embed_query", fake_embed)
    citation = {"raw_text": "Anon. (2020). Title. Publisher.",
                "issues": [{"type": "format_violation", "reason": "Title should be italic"}]}
    assert await rr.search(citation) == []


async def test_search_is_inert_without_an_embedding_key(monkeypatch):
    """No OPENAI_API_KEY must mean zero calls, not a crash -- the two provider
    keys are independent opt-ins."""
    monkeypatch.setattr(cfg, "RAG_ENABLED", False)
    called = False

    async def tracker(*a, **kw):
        nonlocal called
        called = True
        return [0.0] * DIM
    monkeypatch.setattr(rr, "embed_query", tracker)
    assert await rr.search({"raw_text": "x", "issues": []}) == []
    assert not called


async def test_search_returns_top_k_chunks(fake_index, monkeypatch):
    async def fake_embed(text, client=None):
        assert "APA 7th rule for" in text     # the built query, not raw text
        return [0.0, 0.0, 1.0, 0.0]
    monkeypatch.setattr(rr, "embed_query", fake_embed)
    citation = {
        "raw_text": ("Van Vu, D., & Peters, E. (2022). Incidental learning. "
                     "Studies in Second Language Acquisition, 44(3), 685-707."),
        "issues": [{"type": "field_mismatch", "reason": "Author name mismatch"}],
    }
    hits = await rr.search(citation, top_k=2)
    assert len(hits) == 2
    assert hits[0].chunk_id == "apa7-author-name-format"
