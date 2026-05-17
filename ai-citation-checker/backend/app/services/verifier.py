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
from app.storage.db import get_cached_reference, save_verified_reference
from app.config import CROSSREF_MAILTO

CROSSREF_BASE = "https://api.crossref.org/works"
OPENALEX_BASE = "https://api.openalex.org/works"
SCORE_EXACT = 100
SCORE_NEAR_EXACT = 95   # title is essentially identical (small punctuation/spelling diffs)
SCORE_FUZZY_MIN = 85
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


def _is_web_reference(entry: ReferenceEntry) -> bool:
    """Web/organisation citations ('Retrieved from https://...') aren't in
    academic databases — don't bother querying, just flag as unverifiable."""
    txt = entry.raw_text.lower()
    return (
        not entry.doi
        and ("retrieved" in txt or "retrieved from" in txt)
        and "doi.org" not in txt
    )


async def verify_reference(entry: ReferenceEntry, db_path: str) -> VerifyResult:
    # 0. Web/organisation reference — skip verification
    if _is_web_reference(entry):
        return VerifyResult(
            found=False,
            not_found_reason="web/organisation reference — not in academic databases",
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

    return VerifyResult(found=False, not_found_reason=" · ".join(reasons))


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
            "score": 0,
        })

    result = await _score_candidates(items, entry, db_path, source="openalex")
    if result:
        return result, ""
    return None, "score too low or author/year mismatch"


async def _score_candidates(items: list[dict], entry: ReferenceEntry,
                             db_path: str, source: str) -> Optional[VerifyResult]:
    if not items:
        return None

    scored = []
    for item in items:
        cand_title = (item.get("title") or [""])[0]
        # For edited books, Crossref puts editors in `editor`, not `author`.
        cand_authors = item.get("author") or item.get("editor") or []
        cand_year = ((item.get("published") or {}).get("date-parts") or [[0]])[0][0]
        cand_first_author = (cand_authors[0].get("family") or "") if cand_authors else ""

        title_score = fuzz.token_set_ratio(
            entry.title_normalized,
            _norm(cand_title),
        )
        # Treat missing metadata as "unknown" rather than "mismatch" — Crossref
        # often has partial records for book chapters (one entry has authors,
        # another has the year, etc.). We don't want to reject either.
        author_match = (
            not cand_authors
            or _author_surname_match(_norm(cand_first_author), entry.first_author_normalized)
        )
        year_match = cand_year == 0 or abs(cand_year - entry.year) <= 1

        scored.append((title_score, author_match, year_match, item, cand_title))

    # Filter candidates where author+year match (no title threshold for ambiguity detection)
    author_year_matching = [
        (score, item, cand_title)
        for score, author_ok, year_ok, item, cand_title in scored
        # Accept near-exact title + author match even when year disagrees —
        # handles republications (Crossref often has reprint year, not original).
        if author_ok and (year_ok or score >= SCORE_NEAR_EXACT)
    ]

    # Filter to high-confidence matches for the primary result
    matching = [
        (score, item, cand_title)
        for score, item, cand_title in author_year_matching
        if score >= SCORE_FUZZY_MIN
    ]

    if not matching:
        return None

    # Check ambiguity: multiple candidates with same author+year
    ambiguous = len(author_year_matching) > 1
    other_titles = [t for _, _, t in author_year_matching[1:]]

    best_score, best_item, _ = matching[0]
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
    just 'Doehler' — accept that case, but don't accept unrelated names."""
    if not cand or not entry:
        return False
    if cand == entry:
        return True
    cand_words = cand.split()
    entry_words = entry.split()
    return cand_words[-1] == entry_words[-1]


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip()
