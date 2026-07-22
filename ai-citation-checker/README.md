# AI Citation Checker

Upload a `.docx` academic essay and get a colour-annotated report in seconds:
every reference is verified against authoritative databases (Crossref,
OpenAlex, Open Library) and linted against APA 7th formatting rules. Built for
students and researchers — no login, reports auto-delete after 24 hours.

**Live:** https://ai-citation.vercel.app

---

## The idea

Citation lists are tedious to check by hand and easy to get wrong — a
fabricated source, a mismatched year, a journal name that doesn't quite match
the record. This tool does that pass automatically:

- **Reference verification** — resolve each entry against Crossref → OpenAlex
  → Open Library, then compare author, year, title and journal field by field.
- **APA 7th format linting** — 20+ rules (author format, italics, DOI form,
  volume/issue/pages, chapter editors, page ranges, …).
- **In-text matching** — parenthetical `(Smith, 2020)` and narrative
  `Smith (2020)` citations are matched back to the reference list to flag
  orphans and year mismatches.

A three-state colour model keeps it readable: 🟢 verified · 🟡 needs a look ·
🔴 not found.

---

## Roadmap

### ✅ Stage 1 — MVP (shipped)

The locked MVP scope, all live:

- `.docx` upload, APA 7th, web app
- Reference existence + field-match verification
- In-text format + orphan/year checks (now covering narrative form too)
- Annotated report with a 24h shareable link, no accounts

### 🔜 Stage 2 — LLM content match

Go beyond "does this source exist?" to "does the cited paper actually support
the claim?" — an LLM semantic check between the in-text sentence and the
matched reference. Slots in as a new `services/content_matcher.py` and a new
`content_mismatch` issue type; no database change.

Also in this stage: **fix suggestions** and **PDF export** of the report.

### 🔜 Stage 3 — Accounts & history

Optional Google sign-in and a history of past checks. Migrate SQLite →
Postgres, add a `users` table, link reports to a user. Anonymous 24h links
keep working.

### 🧭 Later — Stage 4 and beyond

- **Browser extension** — reuse the existing `/api/check` endpoint behind a
  Manifest V3 frontend
- **More styles** — MLA / Harvard / Chicago alongside APA 7th
- **`.pdf` input**, duplicate-reference detection, localised rule messages

The MVP architecture was designed so each stage slots in without a structural
rewrite.

---

## Tech stack

- **Frontend** — React · Vite · TypeScript · Tailwind (Vercel)
- **Backend** — FastAPI · Python 3.11 · httpx · rapidfuzz (Railway)
- **Storage** — SQLite (24h TTL, scheduled cleanup)

```
frontend/   React + Vite UI (annotated report, zh/en)
backend/
  app/routes/     /api/check, /api/report/:id, /healthz
  app/services/   docx_parser → citation_extractor → verifier → report_builder
  app/rules/      APA 7th format rules
  tests/          pytest — one regression test per real bug
```

---

## Running locally

Requires Python 3.11+ and Node 18+.

```bash
make install   # backend venv + frontend deps
make dev       # frontend :5173 + backend :8000, hot-reload
cd backend && pytest   # run the test suite
```

Uploaded files are parsed in memory and never stored; reports live in SQLite
for 24 hours, then a background job deletes them.
