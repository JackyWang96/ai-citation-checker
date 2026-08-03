# Backlog

Technical debt and follow-ups deferred from focused fixes. Each item notes its
origin so the context isn't lost.

## Plan B — reference-form & journal-structure classification (root refactor)

Surfaced by the Codex × Claude cross-review of `fix/ref06-batch`
(`reviews/review-synthesis.md`, `reviews/codex-second-challenge.md`). The
`ref06` batch patched several symptoms; these are the underlying causes. They
share one root: `_looks_like_book` is an exclusion-based heuristic that also
doubles as Open Library eligibility, so it misclassifies modern journals and
can't be trusted to gate matching precisely.

**Acceptance scope (all must be covered):**

1. **`year_ok` same-year (±1) type bypass.** The book-form article/review guard
   in `_score_candidates` only runs on the year-gap bypass, not on the
   year-matches path — a same-year (±1) same-title article or **book review**
   still matches a book citation via `year_ok`. (Codex #1)
2. **2–4 year near-window subset bypass.** A subset title (`token_set=100`,
   `token_sort<95`) with a 2–4 year gap is still accepted through the
   near-window branch. Confirmed by dynamic probe (`gap=3` → found). (Codex #1)
3. **Split `_looks_like_book` into two concerns:** (a) reference-form
   classification (book / chapter / journal-article / …) and (b) Open Library
   fallback eligibility. Don't treat "has a DOI" as evidence of non-book.
   (Codex #2)
4. **Unified journal-structure recognition** covering the modern
   article-number / eLocator forms (`System, 95, 102366`; `PLOS ONE, 15,
   e0234567`; `15, Article 102366`) so those stop being classified as books —
   which then lets the type guard extend safely to the year_ok path (fixes 1).
   (Codex #2, #5)
5. **OpenAlex schema modernisation.** `host_venue` is removed (use
   `primary_location.source.display_name`); volume/issue/page live in `biblio`
   and must be carried into the canonical record; map OpenAlex `type` values to
   an internal enum (a canonical type adapter, not an ever-growing
   `_ARTICLE_TYPES` list — OpenAlex also has `editorial`/`paratext`/
   `retraction`/…). Re-evaluate whether an `api_key` / `mailto` polite-pool
   param is needed (empirically keyless still returns 200, but the docs now
   list `api_key`; add `mailto` at minimum). (Codex #3, #3b, #3d)
6. **R020 eLocator support** (`15, e0234567`, `Article NNN`) and anchor the
   volume/page presence check to the tail/journal field rather than a
   whole-text search (a title like `Studies 1, 2, 3` currently suppresses
   R020). (Codex #5)
7. **Merged-reference: `(n.d.)` and orphan DOI.** `_looks_like_merged_references`
   keys only on 2+ `(YYYY)`, so a `(2020) + (n.d.)` pair is missed; a stray
   orphan DOI is a real format anomaly worth its own (non-"merged") hint.
   (Codex #4b)
8. **Open Library staged query.** Replace the single loose `q=` with: fielded
   `title`+`author` → main-title (subtitle dropped) fielded → loose `q=`, and
   pick the best candidate by a combined title/author/year score rather than
   the first `token_set` pass. (Codex #6)

**Design direction:** a single canonical adapter that maps Crossref *and*
OpenAlex records into one internal shape (type enum, journal, volume, issue,
page/article-number), plus an accurate reference-form classifier. Once matching
runs on trustworthy classification, the year_ok type guard and R020 both become
correct without per-case patches.
