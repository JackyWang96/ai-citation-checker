from __future__ import annotations
import json
import re
import uuid
import asyncio
from dataclasses import dataclass, field
from typing import Optional
import httpx
from rapidfuzz import fuzz
from app.services.citation_extractor import ReferenceEntry
from app.rules.apa7 import _JOURNAL_STRUCT_RE, _is_chapter
from app.storage.db import get_cached_reference, save_verified_reference
from app.config import CROSSREF_MAILTO

CROSSREF_BASE = "https://api.crossref.org/works"
OPENALEX_BASE = "https://api.openalex.org/works"
OPENLIBRARY_BASE = "https://openlibrary.org/search.json"
SCORE_EXACT = 100
SCORE_NEAR_EXACT = 95   # title is essentially identical (small punctuation/spelling diffs)
SCORE_FUZZY_MIN = 85
# Cap for the "near-exact title bypasses year check" fallback. Reprints / online-
# first vs print drift typically sit within a few years. Beyond this, two works
# by the same author with similar titles are almost always different books
# (e.g. Jiang 2011 "Introducing Second Language Processing" vs Jiang 2018
# "Second Language Processing: An Introduction").
REPRINT_YEAR_GAP_MAX = 5
HTTP_TIMEOUT = 25.0     # Railway → external API latency is much higher than localhost
USER_AGENT = "CitationChecker/1.0 (mailto:jackywangmel96@gmail.com)"


@dataclass
class VerifyResult:
    found: bool
    exact_match: bool = False
    ambiguous: bool = False
    other_titles: list[str] = field(default_factory=list)
    canonical: Optional[dict] = None
    source: Optional[str] = None
    verified_reference_id: Optional[str] = None
    not_found_reason: Optional[str] = None


async def _get_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """GET with exponential backoff on 429 (rate limit). Up to 2 retries."""
    for attempt in range(3):
        resp = await client.get(url, **kwargs)
        if resp.status_code != 429 or attempt == 2:
            return resp
        # Crossref/OpenAlex often include Retry-After; fall back to 1s, 2s
        wait = float(resp.headers.get("Retry-After", str(2 ** attempt)))
        await asyncio.sleep(min(wait, 5.0))
    return resp


# Regex helpers for reference classification.
_SOFTWARE_TAG_RE = re.compile(r'\[\s*(?:computer\s+software|mobile\s+application)\s*\]', re.IGNORECASE)
_PROCEEDINGS_RE = re.compile(
    r'\bproceedings\s+of\b|\b(?:workshop|conference|symposium)\s+on\b',
    re.IGNORECASE,
)


def _classify_reference(entry: ReferenceEntry) -> Optional[str]:
    """Detect reference categories that don't belong in academic databases.

    Returns:
        'software'    — has APA [Computer software] / [Mobile application] tag.
        'web'         — has 'Retrieved from <URL>' and no DOI.
        'proceedings' — conference proceedings without a DOI.
        None          — regular journal/book/chapter; proceed with API lookup.
    """
    txt = entry.raw_text

    if _SOFTWARE_TAG_RE.search(txt):
        return "software"

    txt_lower = txt.lower()
    if (
        not entry.doi
        and "retrieved" in txt_lower
        and "doi.org" not in txt_lower
    ):
        return "web"

    # Proceedings without a DOI — DOI'd proceedings (ACM, IEEE) still go through
    # normal verification because Crossref usually has them.
    if not entry.doi and _PROCEEDINGS_RE.search(txt):
        return "proceedings"

    return None


_CLASSIFICATION_REASONS = {
    "software": "software citation — not in academic databases",
    "web":      "web/organisation reference — not in academic databases",
    "proceedings": "conference proceedings — not typically in academic databases",
}


async def verify_reference(entry: ReferenceEntry, db_path: str) -> VerifyResult:
    # 0. References not expected in academic databases — skip verification
    category = _classify_reference(entry)
    if category:
        return VerifyResult(
            found=False,
            not_found_reason=_CLASSIFICATION_REASONS[category],
        )

    # 1. Cache lookup
    cached = await get_cached_reference(
        db_path, entry.doi, entry.title_normalized,
        entry.first_author_normalized, entry.year,
    )
    if cached:
        return VerifyResult(
            found=True, exact_match=True,
            canonical=json.loads(cached["canonical_json"]),
            source=cached["source"],
            verified_reference_id=cached["id"],
        )

    reasons: list[str] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        # 2. Direct DOI lookup (most reliable — skip fuzzy matching entirely)
        if entry.doi:
            result, reason = await _lookup_by_doi(client, entry, db_path)
            if result:
                return result
            reasons.append(f"DOI lookup: {reason}")
        # 3. Crossref text search
        result, reason = await _search_crossref(client, entry, db_path)
        if result:
            return result
        reasons.append(f"Crossref: {reason}")
        # 4. OpenAlex fallback
        result, reason = await _search_openalex(client, entry, db_path)
        if result:
            return result
        reasons.append(f"OpenAlex: {reason}")
        # 5. Open Library fallback for book-shaped references — pre-DOI books
        #    (e.g. Labov 1972) were never DOI-registered and are missing from
        #    Crossref/OpenAlex, but library catalogues have them.
        if _looks_like_book(entry):
            result, reason = await _search_openlibrary(client, entry, db_path)
            if result:
                return result
            reasons.append(f"Open Library: {reason}")

    return VerifyResult(found=False, not_found_reason=" · ".join(reasons))


def _looks_like_book(entry: ReferenceEntry) -> bool:
    """Book-shaped: no DOI, no journal 'Vol(Issue), pages' structure, and not
    a chapter cite ('. In Editor (Ed.), …')."""
    txt = entry.raw_text.replace("\xa0", " ")
    return (
        not entry.doi
        and not _JOURNAL_STRUCT_RE.search(txt)
        and not _is_chapter(txt)
    )


async def _lookup_by_doi(client: httpx.AsyncClient, entry: ReferenceEntry,
                         db_path: str) -> tuple[Optional[VerifyResult], str]:
    try:
        resp = await _get_with_retry(client, f"{CROSSREF_BASE}/{entry.doi}")
        resp.raise_for_status()
        item = resp.json().get("message", {})
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}"
    except Exception as e:
        return None, str(e)

    if not item:
        return None, "empty response"

    ref_id = str(uuid.uuid4())
    await save_verified_reference(
        db_path, ref_id, entry.doi,
        entry.title_normalized, entry.first_author_normalized, entry.year,
        json.dumps(item), "crossref",
    )
    return VerifyResult(
        found=True, exact_match=True,
        canonical=item, source="crossref",
        verified_reference_id=ref_id,
    ), ""


async def _search_crossref(client: httpx.AsyncClient, entry: ReferenceEntry,
                            db_path: str) -> tuple[Optional[VerifyResult], str]:
    query = f"{entry.title_normalized} {entry.first_author_normalized} {entry.year}"
    try:
        resp = await _get_with_retry(client, CROSSREF_BASE, params={
            "query.bibliographic": query,
            "rows": 5,
            "mailto": CROSSREF_MAILTO,
        })
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}"
    except Exception as e:
        return None, str(e)

    if not items:
        return None, "no results"

    result = await _score_candidates(items, entry, db_path, source="crossref")
    if result:
        return result, ""
    return None, "score too low or author/year mismatch"


async def _search_openalex(client: httpx.AsyncClient, entry: ReferenceEntry,
                            db_path: str) -> tuple[Optional[VerifyResult], str]:
    query = f"{entry.title_normalized} {entry.first_author_normalized}"
    try:
        resp = await _get_with_retry(client, OPENALEX_BASE, params={"search": query, "per-page": 5})
        resp.raise_for_status()
        raw_items = resp.json().get("results", [])
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}"
    except Exception as e:
        return None, str(e)

    if not raw_items:
        return None, "no results"

    items = []
    for w in raw_items:
        title = w.get("title") or ""
        authors = [
            {"family": (parts := a.get("author", {}).get("display_name", "").split()) and parts[-1] or "", "given": ""}
            for a in w.get("authorships", [])
        ]
        year = w.get("publication_year") or 0
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        items.append({
            "title": [title],
            "author": authors,
            "published": {"date-parts": [[year]]},
            "container-title": [w.get("host_venue", {}).get("display_name", "")],
            "DOI": doi,
            # OpenAlex work type (article / book / book-chapter / …) so the
            # book-form article-type guard in _score_candidates applies to
            # OpenAlex candidates too, not just Crossref — otherwise a book
            # citation could still match a same-title journal article here.
            "type": (w.get("type") or "").lower(),
            "score": 0,
        })

    result = await _score_candidates(items, entry, db_path, source="openalex")
    if result:
        return result, ""
    return None, "score too low or author/year mismatch"


async def _search_openlibrary(client: httpx.AsyncClient, entry: ReferenceEntry,
                              db_path: str) -> tuple[Optional[VerifyResult], str]:
    """Look up a book in Open Library (free, keyless, good pre-DOI coverage).

    Stricter than the Crossref/OpenAlex scoring: library catalogues are noisy
    (many editions, user-contributed records), so require a near-exact title
    AND an author match AND a plausible edition year before accepting."""
    # Use the general `q=` search rather than the fielded `title=`+`author=`
    # search: the fielded form misses books whose catalogue title omits the
    # cited subtitle (Bandura 1986 'Social foundations of thought and action:
    # A social cognitive theory' is catalogued without the subtitle → 0 fielded
    # hits). `q=` has higher recall; the strict post-filter below (near-exact
    # title AND author AND year ±1) keeps precision.
    params = {
        "q": f"{entry.title_normalized} {entry.first_author_normalized}",
        "limit": 5,
        "fields": "title,author_name,first_publish_year,publish_year,publisher",
    }
    try:
        resp = await _get_with_retry(client, OPENLIBRARY_BASE, params=params)
        resp.raise_for_status()
        docs = resp.json().get("docs", [])
    except httpx.TimeoutException:
        return None, "timeout"
    except httpx.HTTPStatusError as e:
        return None, f"HTTP {e.response.status_code}"
    except Exception as e:
        return None, str(e)

    if not docs:
        return None, "no results"

    for d in docs:
        title = d.get("title") or ""
        authors = d.get("author_name") or []
        first_author = authors[0] if authors and authors[0] else ""
        fam = first_author.split()[-1] if first_author else ""
        years = list(d.get("publish_year") or [])
        if d.get("first_publish_year"):
            years.append(d["first_publish_year"])

        score = fuzz.token_set_ratio(entry.title_normalized, _norm(title))
        author_ok = bool(fam) and _author_surname_match(
            _norm(fam), entry.first_author_normalized
        )
        year_ok = (
            entry.year == 0
            or any(abs(y - entry.year) <= 1 for y in years if y)
        )
        if score < SCORE_NEAR_EXACT or not author_ok or not year_ok:
            continue

        # Pick the edition year closest to the cited year so the year
        # comparison downstream doesn't raise a spurious warning.
        best_year = (
            min((y for y in years if y), key=lambda y: abs(y - entry.year))
            if years and entry.year else (years[0] if years else 0)
        )
        canonical = {
            "title": [title],
            "author": [{
                "family": fam,
                "given": " ".join(first_author.split()[:-1]),
            }],
            "published": {"date-parts": [[best_year]]},
            "container-title": [""],
            "publisher": (d.get("publisher") or [""])[0],
            "type": "book",
        }
        ref_id = str(uuid.uuid4())
        await save_verified_reference(
            db_path, ref_id, None,
            entry.title_normalized, entry.first_author_normalized, entry.year,
            json.dumps(canonical), "openlibrary",
        )
        return VerifyResult(
            found=True,
            exact_match=score == SCORE_EXACT,
            canonical=canonical,
            source="openlibrary",
            verified_reference_id=ref_id,
        ), ""

    return None, "score too low or author/year mismatch"


async def _score_candidates(items: list[dict], entry: ReferenceEntry,
                             db_path: str, source: str) -> Optional[VerifyResult]:
    if not items:
        return None

    scored = []
    for item in items:
        cand_title = _strip_markup((item.get("title") or [""])[0])
        # For edited books, Crossref puts editors in `editor`, not `author`.
        cand_authors = item.get("author") or item.get("editor") or []
        cand_year = ((item.get("published") or {}).get("date-parts") or [[0]])[0][0]
        cand_first_author = (cand_authors[0].get("family") or "") if cand_authors else ""

        norm_cand = _norm(cand_title)
        title_score = fuzz.token_set_ratio(entry.title_normalized, norm_cand)
        # token_set_ratio returns 100 when one title is a subset of the other
        # (e.g. book 'Self-efficacy: The exercise of control' ⊂ article
        # 'Perceived self-efficacy in the exercise of control over AIDS').
        # token_sort_ratio is length-sensitive, so it stays high only when the
        # titles are genuinely near-identical — used to gate the any-year-gap
        # exact bypass below so a book doesn't match a same-author article/
        # chapter whose title merely contains it.
        title_sort_score = fuzz.token_sort_ratio(entry.title_normalized, norm_cand)
        # Treat missing metadata as "unknown" rather than "mismatch" — Crossref
        # often has partial records for book chapters (one entry has authors,
        # another has the year, etc.). We don't want to reject either.
        author_match = (
            not cand_authors
            or _author_surname_match(_norm(cand_first_author), entry.first_author_normalized)
        )
        year_match = cand_year == 0 or abs(cand_year - entry.year) <= 1

        scored.append((title_score, author_match, year_match, cand_year, item, cand_title, title_sort_score))

    # Filter candidates where author+year match (lenient — used for the primary
    # match, accepts year-missing and near-exact-title cases).
    # A whole-book reference (no DOI, no journal structure, not a chapter cite)
    # must not resolve to a journal article via a year-gap bypass. Classic books
    # (Bandura 1997) share their exact title with same-author *articles* (a book
    # review, a reprint of the title in a journal) that sit a year or two away —
    # accepting those produces a bogus year mismatch and a spurious R020. On the
    # year-matches path there's no such risk, and book-chapter candidates stay
    # allowed so genuine republished chapters (Schegloff 2006 → 2020) still match.
    book_form = _looks_like_book(entry)
    # Non-book work types a whole-book citation must not resolve to via a
    # year-gap bypass. Includes review/peer-review (a book *review* shares the
    # book's exact title — the classic false match) and preprint/posted-content
    # (OpenAlex/Crossref names for the same). 'article' is OpenAlex's spelling
    # of 'journal-article'.
    _ARTICLE_TYPES = {
        "journal-article", "article", "proceedings-article", "report",
        "review", "peer-review", "preprint", "posted-content", "dissertation",
    }

    def _bypass_ok(cand_year: int, score: float, sort_score: float, item: dict) -> bool:
        if cand_year == 0:
            return False
        if book_form and (item.get("type") or "").lower() in _ARTICLE_TYPES:
            return False
        # Identical title (set==100 AND sort>=95): same author + genuinely
        # identical title is overwhelmingly the same work — accept any year drift
        # (reprints decades later). The sort_score gate rejects subset matches
        # where a book title is merely contained in a longer title (Bandura 1986).
        if score >= SCORE_EXACT and sort_score >= SCORE_NEAR_EXACT:
            return True
        # Near-exact title (95–99): probably the same work but possibly a
        # different book on the same topic — cap at REPRINT_YEAR_GAP_MAX years
        # (Jiang 2018 vs 2011).
        return abs(cand_year - entry.year) < REPRINT_YEAR_GAP_MAX and score >= SCORE_NEAR_EXACT

    author_year_matching = [
        (score, item, cand_title)
        for score, author_ok, year_ok, cand_year, item, cand_title, sort_score in scored
        if author_ok and (year_ok or _bypass_ok(cand_year, score, sort_score, item))
    ]

    # Filter to high-confidence matches for the primary result
    matching = [
        (score, item, cand_title)
        for score, item, cand_title in author_year_matching
        if score >= SCORE_FUZZY_MIN
    ]

    if not matching:
        return None

    # When the entry isn't formatted as a chapter cite (no '. In <Book>'),
    # prefer book / journal-article candidates over book-chapter candidates.
    # Crossref often DOI-registers each chapter of a book individually AND
    # returns those chapters before the parent-book record — without this
    # tweak, citing the whole book ('Jiang, N. (2018). Second language
    # processing: An introduction. Routledge.') matches the first chapter
    # ('Introducing Second Language Processing') and produces a misleading
    # title-mismatch warning. Stable sort keeps Crossref's order within each
    # group.
    deprioritise_chapters = ". In " not in entry.raw_text

    def _chapter_key(item: dict) -> bool:
        return (item.get("type") or "").lower() == "book-chapter"

    if deprioritise_chapters:
        matching.sort(key=lambda m: _chapter_key(m[1]))

    # Ambiguity is a stricter check than primary matching: only count candidates
    # whose author, year AND title all firmly match. Year-missing (=0) and
    # year-mismatch-but-near-exact-title cases (preprints/reprints) shouldn't
    # be treated as ambiguous — they're the same work or known different works.
    strict_matches = [
        (score, item, cand_title)
        for score, author_ok, year_ok, cand_year, item, cand_title, sort_score in scored
        if author_ok
        and cand_year != 0
        and abs(cand_year - entry.year) <= 1
        and score >= SCORE_FUZZY_MIN
    ]
    if deprioritise_chapters:
        strict_matches.sort(key=lambda m: _chapter_key(m[1]))

    best_score, best_item, best_title = matching[0]

    # Derive other_titles from strict_matches excluding the chosen primary —
    # never list the primary's own title back to the user as an "other title".
    other_titles = [t for _, _, t in strict_matches if t != best_title]
    ambiguous = len(other_titles) > 0
    exact = best_score == SCORE_EXACT

    ref_id: Optional[str] = None
    if exact:
        ref_id = str(uuid.uuid4())
        doi = best_item.get("DOI") or None
        await save_verified_reference(
            db_path, ref_id, doi,
            entry.title_normalized, entry.first_author_normalized, entry.year,
            json.dumps(best_item), source,
        )

    return VerifyResult(
        found=True,
        exact_match=exact,
        ambiguous=ambiguous,
        other_titles=other_titles,
        canonical=best_item,
        source=source,
        verified_reference_id=ref_id,
    )


async def verify_all(entries: list[ReferenceEntry], db_path: str,
                     concurrency: int = 4) -> list[VerifyResult]:
    # Lower concurrency reduces Crossref rate-limiting (HTTP 429) on shared
    # cloud IPs. Was 10, now 4 — still fast enough for typical paper sizes.
    sem = asyncio.Semaphore(concurrency)

    async def _one(entry: ReferenceEntry) -> VerifyResult:
        async with sem:
            return await verify_reference(entry, db_path)

    return await asyncio.gather(*[_one(e) for e in entries])


def _author_surname_match(cand: str, entry: str) -> bool:
    """Compare normalized surnames, allowing one to drop a leading word.
    Crossref sometimes truncates multi-word surnames like 'Pekarek Doehler' to
    just 'Doehler' — accept that case, but don't accept unrelated names.

    Hyphens are stripped from both sides first: `_norm` deletes them from
    candidate names ('Wenger-Trayner' → 'wengertrayner') while the extractor's
    `first_author_normalized` keeps them ('wenger-trayner'), so every
    hyphenated first author without a DOI failed to match."""
    if not cand or not entry:
        return False
    cand = re.sub(r'[-‐‑]', '', cand)
    entry = re.sub(r'[-‐‑]', '', entry)
    if cand == entry:
        return True
    cand_words = cand.split()
    entry_words = entry.split()
    return cand_words[-1] == entry_words[-1]


_MARKUP_RE = re.compile(r'<[^>]+>')


def _strip_markup(text: str) -> str:
    """Crossref/JATS metadata can embed HTML-ish markup in titles
    (e.g. '<b>lmerTest</b> Package', '<i>in vivo</i>', '<scp>...</scp>').
    Strip the tags before fuzzy-matching or displaying so they don't leak
    into the UI or pollute the match score."""
    return _MARKUP_RE.sub('', text)


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip()
