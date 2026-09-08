"""Retrieval over the committed APA rules index.

The hard-won lesson from the PR 1 recall evaluation is encoded in
`build_query`: **the query must not contain the citation's proper nouns.**
Author names, book titles, and journal names dominate the embedding and bury
the signal that actually matters — what went wrong. Feeding the full
reference text scored 4/6 on top-3 recall; the structured query below scored
6/6 on the same samples.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import openai

import app.config as cfg
from app.rules.apa7 import _is_chapter, _journal_name
from app.services.embeddings import embed_query
from app.services.rules_corpus import corpus_sha256, load_corpus
from app.services.vectors import open_rules_db, pack_vector
from app.services.verifier import _SOFTWARE_TAG_RE

logger = logging.getLogger(__name__)

# Issue types a rewrite can plausibly fix. Kept in sync with fix_suggester:
# retrieval exists to serve that rewrite, so the two must agree on scope.
_FIXABLE_ISSUE_TYPES = frozenset({"format_violation", "field_mismatch"})

# Fingerprint keys that must match between the index and the running config.
# Any mismatch means the query vectors and the indexed vectors are not
# comparable. `normalized` is the dangerous one: dimensions still line up, no
# error is raised, and the ranking is silently wrong.
_FINGERPRINT_KEYS = (
    "embedding_provider",
    "embedding_model",
    "embedding_dim",
    "normalized",
    "corpus_sha256",
)

# 'System, 95, 102366.' — the modern article-number / eLocator form: volume
# followed by a bare number instead of a page range. `_journal_name` cannot
# see these (it requires a page range), so without this they get misread as
# books — exactly the shape R020 fires on.
_ARTICLE_NUMBER_RE = re.compile(
    r',\s*\d+\s*(?:\(\s*\d+[a-zA-Z]?\s*\))?\s*,\s*\d+\s*(?:\.|$)'
)

_SEARCH_SQL = """
    SELECT c.chunk_id, c.title, c.text, c.source_url, v.distance
    FROM rule_vectors v
    JOIN rule_chunks c ON c.rowid = v.rowid
    WHERE v.embedding MATCH ? AND k = ?
    ORDER BY v.distance
"""


@dataclass(frozen=True)
class RuleChunk:
    chunk_id: str
    title: str
    text: str
    source_url: str
    distance: float


def type_hints(raw_text: str) -> str:
    """Name the reference's *shape* without naming anything in it.

    Derived from structure only — no author, title, journal, or publisher text
    is ever echoed back, because those are exactly what poisoned the naive
    query.

    Deliberately terse. An earlier version returned rich descriptions
    ("chapter in an edited book; editors are named; page range written with
    pp.") and measurably did worse: 5/6 top-3 versus 6/6. A long hint carries
    more lexical weight than a three-word problem description like "Author
    name mismatch", so the query drifts toward whatever chunk matches the
    *type* and away from the chunk that answers the actual problem.
    """
    text = raw_text.replace("\xa0", " ")
    if _is_chapter(text):
        # Editor order and pp. formatting are already stated by the problem
        # description; repeating them here only adds weight.
        return "edited book chapter"
    if _journal_name(text):
        return "journal article"
    if _ARTICLE_NUMBER_RE.search(text):
        return "journal article with an article number"
    if _SOFTWARE_TAG_RE.search(text):
        return "software"
    if "(n.d.)" in text.lower():
        return "undated source"
    if "http" in text.lower():
        return "web page"
    return "book"


def build_query(citation: dict) -> str:
    """Query = what the checker found wrong + what shape the reference is.

    Deliberately excludes `raw_text`, and excludes each issue's `expected` /
    `actual` values too — those carry surnames and journal names, which is the
    same proper-noun contamination in a smaller package.
    """
    reasons = [
        r
        for i in citation.get("issues") or []
        if i.get("type") in _FIXABLE_ISSUE_TYPES and (r := i.get("reason"))
    ]
    reasons = [r.rstrip(". ") for r in reasons]
    problems = ". ".join(reasons) if reasons else "formatting problem"
    hint = type_hints(citation.get("raw_text", ""))
    # "APA 7th rule for:" frames the query as a request for guidance rather
    # than as a defect report, pulling it toward the rule chunks. Measured
    # 6/6 top-3 against 5/6 without the framing.
    return f"APA 7th rule for: {problems}. Reference type: {hint}."


def verify_rules_index(db: sqlite3.Connection, corpus_dir: str) -> None:
    """Fail fast when the index cannot be trusted for the running config.

    This is deliberately a hard error, not a degradation: a mismatch is a
    deployment mistake (someone edited the corpus, or changed the embedding
    model, without rebuilding), and serving confidently-ranked wrong rules is
    worse than serving none. Callers that must stay up catch it and fall back
    to no retrieval — see `search`.
    """
    meta = dict(db.execute("SELECT key, value FROM index_meta").fetchall())
    runtime = {
        "embedding_provider": cfg.EMBEDDING_PROVIDER,
        "embedding_model": cfg.EMBEDDING_MODEL,
        "embedding_dim": str(cfg.EMBEDDING_DIM),
        "normalized": str(cfg.EMBEDDING_NORMALIZED).lower(),
        "corpus_sha256": corpus_sha256(load_corpus(Path(corpus_dir))),
    }
    mismatches = [
        f"{k}: index={meta.get(k)!r} runtime={runtime[k]!r}"
        for k in _FINGERPRINT_KEYS
        if meta.get(k) != runtime[k]
    ]
    if mismatches:
        raise RuntimeError(
            "APA rules index is incompatible with the current configuration; "
            "rebuild with `python -m app.scripts.build_rules_index`. "
            f"[index built_at={meta.get('built_at')} "
            f"sqlite_vec={meta.get('sqlite_vec_version')}] " + " | ".join(mismatches)
        )
    db.execute("SELECT vec_version()")  # prove the extension really loaded


_db: Optional[sqlite3.Connection] = None
_db_lock = threading.Lock()


def open_index() -> sqlite3.Connection:
    """Process-wide read-only index handle, verified once on first use."""
    global _db
    with _db_lock:
        if _db is None:
            db = open_rules_db(cfg.RULES_DB_PATH, read_only=True)
            try:
                verify_rules_index(db, cfg.RULES_CORPUS_DIR)
            except Exception:
                db.close()
                raise
            _db = db
    return _db


def search_vector(
    db: sqlite3.Connection, vector: list[float], top_k: int
) -> list[RuleChunk]:
    rows = db.execute(_SEARCH_SQL, (pack_vector(vector), top_k)).fetchall()
    return [RuleChunk(*row) for row in rows]


async def search(
    citation: dict,
    *,
    top_k: Optional[int] = None,
    client: Optional[openai.AsyncOpenAI] = None,
) -> list[RuleChunk]:
    """Top-k rule chunks for one flagged citation, or [] if retrieval fails.

    Fails open by design: a missing key, a broken index, or an embedding
    outage must degrade the suggestion to its current non-RAG quality rather
    than take the feature down. The caller cannot distinguish "no rules
    found" from "retrieval broke", and does not need to — both mean "build
    the prompt without a rules section".
    """
    if not cfg.RAG_ENABLED:
        return []
    try:
        vector = await embed_query(build_query(citation), client=client)
        return search_vector(open_index(), vector, top_k or cfg.RAG_TOP_K)
    except Exception:
        logger.warning(
            "rules retrieval failed; degrading to non-RAG suggestion", exc_info=True
        )
        return []
