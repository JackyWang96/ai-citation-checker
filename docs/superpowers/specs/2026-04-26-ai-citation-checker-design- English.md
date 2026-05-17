# AI Citation Checker — Design Spec

**Date:** 2026-04-26
**Author:** Jacky Wang
**Status:** Draft (awaiting review)

---

## 1. Background & Problem

University students increasingly use AI (ChatGPT, Claude, Gemini) to draft essays. AI-generated essays frequently contain three classes of citation problems:

1. **Fabricated references** — the cited paper does not exist (LLM hallucination).
2. **Incorrect metadata** — the paper exists but author / year / journal / page numbers are wrong, or the in-text citation does not match the reference list.
3. **Format errors** — citation style (APA / MLA / etc.) rules are violated (italics, hanging indent, author initials, DOI format).

Students currently catch these issues only when a marker flags them — too late. There is no fast, free tool that audits an essay's citations end-to-end before submission.

## 2. Goal

Ship a web application where a student uploads a `.docx` essay, and within ~5 seconds receives a colour-annotated report showing every citation classified as **pass / warning / error**, with explanations they can act on.

## 3. Non-Goals (MVP)

- Multi-format citation styles (MLA / Harvard / Chicago / IEEE) — APA 7th only.
- Content matching ("does the cited paper actually support this claim?") — deferred to Stage 2 (requires LLM).
- **In-text citation content validation** — we do NOT compare in-text year/page against the reference list entry; only format and existence-in-reference-list are checked.
- PDF input — `.docx` only.
- User accounts, history, payments.
- Auto-fix / one-click correction.
- Browser extension / Google Docs integration.

## 4. MVP Scope (Locked)

| Dimension | MVP | Next Stage |
|---|---|---|
| Product form | Web app | Browser extension |
| Citation style | APA 7th | + MLA / Harvard / Chicago |
| Detection layers | Reference: existence + field match; In-text: format + orphan only | + In-text content match (year/page); + LLM semantic match |
| Report form | Annotated text (colour + click for detail) | + Fix suggestions + PDF export |
| Document format | `.docx` | + `.pdf` |
| Auth / storage | No login + 24h shareable link | Optional accounts + history |

## 5. User Flow

1. Student opens the home page → drag-and-drop `essay.docx`.
2. Frontend `POST /api/check` (multipart upload).
3. Backend parses, extracts citations, verifies against Crossref, runs APA validator, builds report JSON, persists to SQLite with `expires_at = now + 24h`.
4. Backend returns `{ report_id, url: "/r/<uuid>" }`.
5. Frontend redirects to `/r/<uuid>`. Page renders:
   - **Left panel**: full essay text, every in-text citation wrapped in a coloured `<span>` (green / yellow / red).
   - **Right panel**: scrollable issue list; clicking an issue scrolls to and highlights the citation in the left panel.
   - **Top bar**: total score + counts (`12 pass / 2 warning / 1 error`).
6. Link is shareable for 24 hours, then a background job deletes the row.

## 6. Architecture

### 6.1 System Diagram

```
┌─────────────────────────────────────────────┐
│  Browser  (React + Vite + TypeScript)        │
│  Upload page → Progress → Annotated report   │
└──────────────────┬──────────────────────────┘
                   │ HTTPS (Vercel-hosted)
                   ▼
┌─────────────────────────────────────────────┐
│  FastAPI  (Python 3.11, Railway-hosted)     │
│  ┌────────────────────────────────────────┐ │
│  │ routes/   upload.py · report.py        │ │
│  │ services/ docx_parser · citation_      │ │
│  │           extractor · verifier · apa_  │ │
│  │           validator · report_builder   │ │
│  │ storage/  db.py · cleanup.py           │ │
│  │ rules/    apa7.py                      │ │
│  └────────────────────────────────────────┘ │
└──────┬──────────────────────────────┬───────┘
       │                              │
       ▼                              ▼
┌──────────────┐               ┌──────────────┐
│ SQLite       │               │ Crossref API │
│ /data/       │               │ (free)       │
│ reports.db   │               │              │
└──────────────┘               └──────────────┘
```

### 6.2 Module Responsibilities

| Module | Responsibility | Key library |
|---|---|---|
| `docx_parser.py` | Read uploaded `.docx` from memory (BytesIO, never written to disk), extract paragraphs preserving italic runs (needed for journal-name validation), detect the "References" / "Bibliography" / "Works Cited" heading. | `python-docx` |
| `citation_extractor.py` | (a) Regex-match in-text citations: `(Smith, 2020)`, `(Smith & Jones, 2020, p. 15)`, `(Smith et al., 2020)`. (b) Split the references section into individual reference entries by hanging-indent / blank-line heuristic. | stdlib `re` |
| `verifier.py` | For each reference: extract title + first author + year, query Crossref `/works?query.bibliographic=...`, fuzzy-match top results against student input, return matched DOI + canonical metadata. Concurrency via `asyncio.gather` (up to 10 in-flight). Fallback to OpenAlex if Crossref returns nothing. | `httpx`, `rapidfuzz` |
| `apa_validator.py` | Apply APA 7th rule set against each reference (author format, year parens, italics, DOI format, hanging indent). Pure local, no I/O. | rules in `rules/apa7.py` |
| `report_builder.py` | Cross-reference in-text citations against reference list (orphans / mismatched years), aggregate issues per citation, compute summary counts, produce `Report` JSON. | Pydantic |
| `storage/db.py` | Async SQLite via `aiosqlite`, single table `reports`, no ORM. | `aiosqlite` |
| `storage/cleanup.py` | APScheduler hourly job: `DELETE FROM reports WHERE expires_at < ?`. | `APScheduler` |

### 6.3 Data Model

```python
class CitationIssue(BaseModel):
    type: Literal["not_found", "field_mismatch", "format_violation", "orphan", "ambiguous"]
    severity: Literal["red", "yellow"]
    category: Literal["content", "format", "orphan", "ambiguous"]   # for UI grouping
    reason: str             # one-line explanation shown in annotation tooltip
    detail: Optional[str]   # supplementary info (e.g. other papers that year, rule reference)
    field: Optional[str]    # field with the issue: "author" / "year" / "journal" etc.
    expected: Optional[str] # canonical value
    actual: Optional[str]   # what student wrote
    rule_id: Optional[str]  # APA rule ID for format violations e.g. "R003"

class Citation(BaseModel):
    id: str                 # "c1", "c2", ...
    kind: Literal["intext", "reference"]
    raw_text: str           # exact substring as written by student
    char_start: int         # offset into full_text (for highlighting)
    char_end: int
    status: Literal["pass", "warning", "error"]
    issues: list[CitationIssue]
    verified_reference_id: Optional[str]  # FK to verified_references.id (GREEN only; None for YELLOW and RED)

class Report(BaseModel):
    id: str                 # 128-bit UUID, URL-safe
    filename: str
    created_at: datetime
    expires_at: datetime
    full_text: str
    citations: list[Citation]
    summary: dict           # {"total": 15, "pass": 10, "warning": 3, "error": 2}
```

SQLite schema:

```sql
CREATE TABLE reports (
    id          TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    filename    TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    expires_at  TIMESTAMP NOT NULL
);
CREATE INDEX idx_reports_expires_at ON reports(expires_at);

-- Permanent cache of GREEN-only verified references.
-- Written only when title score=100 AND author AND year all match exactly.
-- Reused by subsequent reports citing the same paper, avoiding redundant API calls.
CREATE TABLE verified_references (
    id                      TEXT PRIMARY KEY,
    doi                     TEXT UNIQUE,          -- preferred cache key (nullable)
    title_normalized        TEXT NOT NULL,        -- lowercased, punctuation stripped
    first_author_normalized TEXT NOT NULL,        -- lowercased surname
    year                    INTEGER NOT NULL,
    canonical_json          TEXT NOT NULL,        -- full Crossref/OpenAlex response
    source                  TEXT NOT NULL,        -- "crossref" | "openalex"
    cached_at               TIMESTAMP NOT NULL
);
CREATE INDEX idx_vr_doi    ON verified_references(doi) WHERE doi IS NOT NULL;
CREATE INDEX idx_vr_lookup ON verified_references(title_normalized, first_author_normalized, year);
```

### 6.4 API Surface

| Method | Path | Request | Response |
|---|---|---|---|
| POST | `/api/check` | `multipart/form-data` with `file` | `{ "report_id": "...", "url": "/r/..." }` |
| GET | `/api/report/{id}` | — | `Report` JSON, or `410 Gone` if expired |
| GET | `/healthz` | — | `{ "ok": true }` (Railway healthcheck) |

### 6.5 Frontend

- **Stack**: React 18 + Vite + TypeScript + Tailwind CSS.
- **Pages**: `Upload.tsx`, `Report.tsx`.
- **Key components**:
  - `AnnotatedText.tsx` — renders `full_text` with `<Citation>` spans inserted at `char_start..char_end`.
  - `IssuePanel.tsx` — virtualised list (large reports) of issues, click → scroll to citation.
  - `Citation.tsx` — coloured span, hover tooltip with first issue, click → focus right panel.
- **Routing**: `react-router-dom`. `/r/:id` resolves to `Report` page.

## 7. Detection Logic Detail

The MVP uses a **simplified three-state colour model**:

- 🔴 **RED** — only triggered when the reference does not exist online (Crossref + OpenAlex both miss).
- 🟡 **YELLOW** — reference exists but has any field discrepancy (content) or APA format violation; or in-text citation has format issue / cannot be resolved to a reference list entry.
- 🟢 **GREEN** — reference exists with all fields matching; in-text citation has correct format and a matching reference list entry.

### 7.1 Reference list — Existence check (with cache)

For each parsed reference:

1. Extract `doi` (if present in student text), `title`, `first_author`, `year`.
2. **Cache lookup** (hits skip Crossref entirely):
   - If DOI present → `SELECT * FROM verified_references WHERE doi = ?`
   - Otherwise → `SELECT * FROM verified_references WHERE title_normalized = ? AND first_author_normalized = ? AND year = ?`
   - Cache hit → use `canonical_json`, proceed to §7.2.
3. **Cache miss** → call Crossref:
   - `https://api.crossref.org/works?query.bibliographic=<title+author+year>&rows=5&mailto=<contact>`
   - The `mailto` places us in the Crossref polite pool (~100 req/s).
4. For each candidate: compute `rapidfuzz.token_set_ratio(student_title, candidate_title)`.
5. `score = 100` AND author matches AND `|year| <= 1` → exact match (GREEN candidate).
   - Write to `verified_references` (source = "crossref"), proceed to §7.2.
   `score 85-99` → possible match (YELLOW candidate), **do NOT write to cache**, proceed to §7.2.
6. No Crossref match → fall back to OpenAlex (`https://api.openalex.org/works?search=...`).
   - Exact match (score=100) → write to `verified_references` (source = "openalex"), proceed to §7.2.
   - Fuzzy match (85-99) → skip cache, proceed to §7.2.
7. Both fail → proceed to AI fabrication signal detection (§7.5).

### 7.2 Reference list — Field-level comparison

When a canonical record is attached, compare field by field. **Any** mismatch produces a 🟡 YELLOW issue. Each issue is tagged as either `category: "content"` or `category: "format"` so the UI can group them.

| Field | Comparison rule | Issue category |
|---|---|---|
| First author surname | exact (case-insensitive) | content |
| All author surnames | exact (case-insensitive), in order | content |
| Year | exact | content |
| Title | `token_set_ratio >= 95` | content |
| Journal / container | `token_set_ratio >= 90` | content |
| Volume | exact if student provided | content |
| Issue | exact if student provided | content |
| Pages | exact if student provided | content |
| DOI | exact if student provided | content |

A reference is 🟢 GREEN only when (a) it was found, AND (b) zero content-mismatch issues, AND (c) zero APA format issues (§7.4) on its paragraph.

### 7.3 In-text citations — Format & existence

In-text citations are validated for format compliance only — we do NOT verify their content against the reference list (e.g. year/page mismatches between `(Smith, 2020, p. 15)` and `Smith (2020). ... pp. 12-30` are NOT checked in MVP).

For each in-text citation extracted from the body:

| Check | Rule | Result on failure |
|---|---|---|
| **APA bracket format** | Matches `(Author[, Author...]\s*,\s*\d{4}([a-z])?(\s*,\s*p+\.\s*\d+)?)` or narrative form `Author (Year)` | 🟡 YELLOW (`category: "format"`) — e.g. missing parens, missing comma, wrong case |
| **Author casing** | Surname starts with capital letter | 🟡 YELLOW (format) |
| **Three-or-more authors** | Uses `et al.` from the first citation onward (APA 7th) | 🟡 YELLOW (format) |
| **Existence in reference list** | The cited author + year combination matches at least one reference list entry | 🟡 YELLOW (`category: "orphan"`) — "in-text 找不到对应 reference" |

In-text citations that pass all checks → 🟢 GREEN.

**Out of scope for MVP**: comparing in-text year/page against the matched reference list entry's canonical year/page.

### 7.4 APA 7th format rules (reference list paragraphs)

Encoded as pure functions in `rules/apa7.py`. Each rule takes a parsed reference paragraph and returns `Optional[CitationIssue]` with `category: "format"`. Any triggered rule downgrades the reference to 🟡 YELLOW (cannot be GREEN).

- `R001` Author format `Last, F. M.` (initials with periods).
- `R002` Year in parentheses immediately after authors.
- `R003` Journal title italicised (check `run.italic` from python-docx).
- `R004` Volume number italicised; issue number in parentheses, not italicised.
- `R005` DOI as URL `https://doi.org/...`, not `doi:...`.
- `R006` Hanging indent on reference paragraphs (`paragraph_format.first_line_indent < 0`).
- `R007` Multiple authors separated by commas, last by `, & `.

Rules can be added incrementally; each is independently testable.

### 7.5 Status decision summary

```
Reference entry status:
  not found in Crossref+OpenAlex                        → 🔴 RED
  found, but any content mismatch                       → 🟡 YELLOW (content)
  found, but any APA format violation                   → 🟡 YELLOW (format)
  found, but multiple papers by same author+year exist  → 🟡 YELLOW (ambiguous)
  found, all match, no format issues, no ambiguity      → 🟢 GREEN

In-text citation status:
  format violates APA bracket rules   → 🟡 YELLOW (format)
  no matching entry in reference list → 🟡 YELLOW (orphan)
  otherwise                           → 🟢 GREEN
```

### 7.6 Annotation detail display

Each citation shows structured reasons on click. Format per colour:

**🔴 RED**
```
Reference not found
├── No match in Crossref or OpenAlex
├── Author "Smyth, J." has no publications in academic databases
└── Journal "Journal of AI Studies" does not exist
```

**🟡 YELLOW — content**
```
Fields differ from canonical record
├── Author: you wrote "Smyth", canonical is "Smith"
├── Year: you wrote "2019", canonical is "2020"
└── Journal: you wrote "Jornal of...", canonical is "Journal of..."
```

**🟡 YELLOW — format**
```
APA 7th format violations
├── [R003] Journal title must be italicised
├── [R005] DOI must use https://doi.org/... format
└── [R006] Reference paragraph requires hanging indent
```

**🟡 YELLOW — ambiguous**
```
Same author published multiple papers in this year — please verify
├── Matched: "AI in Education"
├── Other papers that year: "AI in Learning Outcomes", "AI and Pedagogy"
└── Tip: adding a DOI to your reference removes ambiguity
```

**🟡 YELLOW — orphan**
```
No matching entry in Reference List
└── In-text citation (Smith, 2020) found, but no Smith 2020 entry in Reference List
```

**🟢 GREEN**
```
✓ Reference verified (source: Crossref)
✓ Author, year, title, and journal match canonical record
✓ APA format correct
```


### 7.6 Ambiguity detection — same author, same year, multiple papers

When Crossref returns multiple candidates where more than one matches the student's author + year, append an ambiguity warning even if one title already matched:

**Trigger condition:**
```python
matched_candidates = [c for c in crossref_results if author_match(c) and year_match(c)]
if len(matched_candidates) > 1:
    # append CitationIssue(type="ambiguous", category="ambiguous", severity="yellow")
```

**Report display:**
```
🟡 YELLOW (ambiguous)
"Smith, J. published multiple papers in 2020. Please verify you cited the correct one.
  Matched: 'AI in Education'
  Other papers that year:
    · 'AI in Learning Outcomes'
    · 'AI and Pedagogy'"
```

**DOI nudge shown in report header when ambiguity is detected:**
```
💡 Tip: Adding a DOI to your reference uniquely identifies the paper and removes ambiguity.
   APA 7th requires a DOI when one is available.
```

**Why not auto-select the closest match:** In ambiguous cases, any automatic selection risks picking the wrong paper. Returning the decision to the student is safer than a confident wrong guess.

## 8. Performance Budget

For a representative 2000-word essay with 15 references:

| Stage | Target | Notes |
|---|---|---|
| `.docx` parse | < 300 ms | python-docx is single-threaded |
| Citation extraction | < 100 ms | regex on plain text |
| Crossref verification | < 4 s | concurrency 10, dominated by slowest API call |
| APA validation | < 100 ms | local |
| Persist + return | < 200 ms | SQLite write |
| **End-to-end** | **< 5 s** | |

Frontend should show a determinate progress bar driven by Server-Sent Events or simple polling on `/api/report/{id}` (status field added if needed).

## 9. Privacy & Security

- The uploaded `.docx` is parsed in memory and **never written to disk**. Only the structured report JSON is persisted.
- Report IDs are 128-bit UUIDs (unguessable, no auth needed).
- All reports auto-delete after 24h via APScheduler.
- HTTPS enforced by Railway / Vercel.
- CORS allowlist: only the production frontend origin.
- No PII collected; no analytics by default.
- Rate limit `POST /api/check` at 10 req/min/IP via `slowapi` (prevents abuse of free Crossref quota).

## 10. Deployment

| Layer | Platform | Cost |
|---|---|---|
| Frontend | Vercel (free tier, static + CDN) | $0 |
| Backend | Railway Hobby (always-on container, 1× service) | ~$5/mo |
| Database | SQLite on Railway Volume (1 GB) | included |
| Domain | Cloudflare (`.com`) | $10/yr |

CI: GitHub Actions runs `pytest` + `ruff` + frontend `vitest` + `tsc` on every PR. Railway auto-deploys backend on push to `main`; Vercel auto-deploys frontend.

## 11. Testing Strategy

- **Unit tests** (pytest): each service module, especially `citation_extractor` (regex edge cases) and `apa7` rules. Target 80%+ coverage.
- **Integration tests**: end-to-end on a small corpus of fixture `.docx` essays in `tests/fixtures/`:

| Fixture | Scenario |
|---|---|
| `clean.docx` | All citations correct → all green |
| `fabricated.docx` | Completely fabricated references (miss on Crossref + OpenAlex) → all red |
| `format_errors.docx` | References exist but APA violations (author initials, italics, DOI format) → yellow |
| `metadata_mismatch.docx` | References exist but author/year/journal wrong → yellow |
| `ambiguous_author.docx` | Same author, same year, multiple real papers; student title matches one → yellow (ambiguous warning) |
| `missing_subtitle.docx` | Student omits subtitle (score 85-99) → yellow (title incomplete) |
| `orphan_intext.docx` | In-text citation has no matching reference list entry → yellow (orphan) |
| `mixed.docx` | Combination of the above |

- **Crossref mocking**: HTTP responses recorded via `respx` so tests are deterministic and offline. The `ambiguous_author` fixture mocks Crossref returning multiple candidates from the same author and year.
- **Frontend**: vitest for components; Playwright smoke test for upload → report flow.

## 12. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Crossref rate-limit | Long essays fail | Polite pool (`mailto`), exponential backoff, per-IP rate limit |
| Crossref miss for non-English / niche works | False "not found" | OpenAlex fallback |
| Malformed `.docx` (images, tables, equations) | Parser crash | `try/except` around parser, return user-friendly error |
| Reference section heading variant ("Bibliography", no heading) | Empty reference list | Multi-heading regex + last-N-paragraphs heuristic |
| Single-process bottleneck | Concurrent users queue | Uvicorn `--workers 4` (Railway 512MB tier supports this) |

## 13. Evolution Path (Stages 2 → 4)

The MVP architecture supports each next stage **without** structural rewrite:

- **Stage 2 — LLM content match**: add `services/content_matcher.py` calling Claude API; new `CitationIssue.type = "content_mismatch"`. No DB schema change.
- **Stage 3 — Accounts & history**: migrate SQLite → Postgres (drop-in via SQLAlchemy or raw SQL), add `users` table + Google OAuth, link `reports.user_id`.
- **Stage 4 — Browser extension**: extension reuses existing `/api/check` endpoint; only a new Manifest V3 frontend.

## 14. Open Questions

None at design time — all major decisions locked through brainstorming. Implementation plan will surface tactical questions (specific regex patterns, exact Crossref response shape edge cases).

---

## Appendix A — Default Rate Limits

- Crossref polite pool: 100 req/s sustained, ~50 baseline.
- OpenAlex: 100k req/day free, 10 req/s burst.
- Our public rate limit: 10 essays/min/IP (≈ 150 Crossref calls/min worst case, well under quota).

## Appendix B — File Layout

```
ai-citation-checker/
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── routes/
│   │   │   ├── upload.py
│   │   │   └── report.py
│   │   ├── services/
│   │   │   ├── docx_parser.py
│   │   │   ├── citation_extractor.py
│   │   │   ├── verifier.py
│   │   │   ├── apa_validator.py
│   │   │   └── report_builder.py
│   │   ├── storage/
│   │   │   ├── db.py
│   │   │   └── cleanup.py
│   │   ├── models/
│   │   │   └── schemas.py
│   │   └── rules/
│   │       └── apa7.py
│   └── tests/
│       ├── test_extractor.py
│       ├── test_verifier.py
│       ├── test_apa7.py
│       └── fixtures/
│           ├── clean.docx
│           ├── fabricated.docx
│           └── format_errors.docx
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tailwind.config.js
    └── src/
        ├── App.tsx
        ├── main.tsx
        ├── pages/
        │   ├── Upload.tsx
        │   └── Report.tsx
        ├── components/
        │   ├── AnnotatedText.tsx
        │   ├── IssuePanel.tsx
        │   └── Citation.tsx
        └── lib/
            └── api.ts
```
