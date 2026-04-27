# Changelog

Changes are recorded by date (AEST).

## 2026-04-28

**Frontend redesign — match high-fidelity design handoff**

- CSS design tokens: oklch palette, Plus Jakarta Sans + JetBrains Mono fonts
- Upload page: frosted-glass navbar, hero section (Chinese copy), drag-and-drop zone with dragging state, feature pills
- New Loading page: spinner, 5-step animated checklist, progress bar; real upload runs in parallel with animation
- Report page: top bar with donut score gauge, pass/warning/error counts, DOI tip banner; split-panel layout
- Cross-highlighting: clicking citation span scrolls to issue card and vice versa; hover highlights propagate both ways
- AnnotatedText: annotated essay body + structured References section (border-left styled blocks)
- Citation: coloured underline, hover/active bg, tooltip shows first issue reason
- New components: IssueCard, StatusBadge, CategoryTag
- IssuePanel: filter tabs (全部/错误/警告/通过) with counts, issue cards, expiry timer + copy-share-link footer

## 2026-04-27

**AI Citation Checker — full MVP implementation**

- Scaffold: FastAPI backend + React 19/Vite/TypeScript/Tailwind frontend monorepo
- Pydantic data models: CitationIssue, Citation, Report
- SQLite storage with WAL mode, 24h report expiry, verified_references cache table
- DOCX parser: in-memory parsing, reference section detection, italic run extraction
- Citation extractor: in-text (Author, year) regex + reference list parser with normalization
- APA 7th rules R001-R007: author format, year parens, journal italic, hanging indent, DOI format, author separator
- Verifier: Crossref primary + OpenAlex fallback, fuzz scoring (token_set_ratio), GREEN-only caching, ambiguity detection
- Report builder: field-level comparison, orphan in-text detection, status aggregation
- FastAPI routes: POST /api/check, GET /api/report/{id}, GET /healthz, rate limiting (slowapi 10/min), CORS from env var
- APScheduler hourly cleanup of expired reports
- Upload page: drag-and-drop .docx, progress spinner, navigate to report on success
- Report page: annotated full text with coloured citation highlights, right-panel issue list with expected/actual diff
- CI/CD: GitHub Actions (backend ruff+pytest, frontend tsc+build), Dockerfile, railway.toml, VOLUME /data
- Production fixes: size check before buffering via Content-Length, CORS origin from ALLOWED_ORIGIN env var, single uvicorn worker for SQLite safety
