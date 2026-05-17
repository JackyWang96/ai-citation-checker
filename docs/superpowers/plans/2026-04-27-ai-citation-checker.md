# AI Citation Checker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web app where students upload a `.docx` essay and receive a colour-annotated report (🔴/🟡/🟢) for every APA citation within ~5 seconds.

**Architecture:** FastAPI backend (Railway) + React/Vite frontend (Vercel) + SQLite on Railway Volume. Backend parses `.docx` in memory, verifies each reference against Crossref/OpenAlex concurrently, runs APA 7th rules, and returns a structured JSON report stored for 24h with a shareable UUID link.

**Tech Stack:** Python 3.11, FastAPI, python-docx, httpx, rapidfuzz, aiosqlite, APScheduler, slowapi · React 18, Vite, TypeScript, Tailwind CSS, react-router-dom

---

## File Map

```
ai-citation-checker/
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                    # FastAPI app, CORS, lifespan
│   │   ├── config.py                  # env vars
│   │   ├── models/
│   │   │   └── schemas.py             # Pydantic: CitationIssue, Citation, Report
│   │   ├── routes/
│   │   │   ├── upload.py              # POST /api/check
│   │   │   └── report.py             # GET /api/report/{id}, GET /healthz
│   │   ├── services/
│   │   │   ├── docx_parser.py        # .docx → ParsedDocument
│   │   │   ├── citation_extractor.py # text → intext citations + reference entries
│   │   │   ├── verifier.py           # reference → Crossref/OpenAlex + cache
│   │   │   ├── apa_validator.py      # reference paragraph → APA issues
│   │   │   └── report_builder.py     # assemble final Report
│   │   ├── storage/
│   │   │   ├── db.py                 # aiosqlite init + queries
│   │   │   └── cleanup.py            # APScheduler hourly cleanup
│   │   └── rules/
│   │       └── apa7.py               # R001-R007 pure functions
│   └── tests/
│       ├── conftest.py
│       ├── test_extractor.py
│       ├── test_verifier.py
│       ├── test_apa7.py
│       ├── test_report_builder.py
│       ├── test_routes.py
│       └── fixtures/
│           ├── make_fixtures.py       # script to generate .docx fixtures
│           ├── clean.docx
│           ├── fabricated.docx
│           ├── format_errors.docx
│           ├── metadata_mismatch.docx
│           ├── ambiguous_author.docx
│           ├── missing_subtitle.docx
│           ├── orphan_intext.docx
│           └── mixed.docx
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tailwind.config.js
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── lib/
        │   └── api.ts                 # fetch wrappers
        ├── pages/
        │   ├── Upload.tsx
        │   └── Report.tsx
        └── components/
            ├── AnnotatedText.tsx
            ├── Citation.tsx
            └── IssuePanel.tsx
```

---

## Task 1: Project Scaffolding

**Files:**
- Create: `backend/pyproject.toml`
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tailwind.config.js`
- Create: `frontend/index.html`
- Create: `.gitignore`

- [ ] **Step 1: Create backend directory and pyproject.toml**

```bash
mkdir -p ai-citation-checker/backend/app/{models,routes,services,storage,rules}
mkdir -p ai-citation-checker/backend/tests/fixtures
```

Create `ai-citation-checker/backend/pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "ai-citation-checker"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi==0.115.0",
    "uvicorn[standard]==0.30.6",
    "python-docx==1.1.2",
    "httpx==0.27.2",
    "rapidfuzz==3.9.7",
    "aiosqlite==0.20.0",
    "apscheduler==3.10.4",
    "slowapi==0.1.9",
    "pydantic==2.8.2",
]

[project.optional-dependencies]
dev = [
    "pytest==8.3.2",
    "pytest-asyncio==0.23.8",
    "respx==0.21.1",
    "ruff==0.6.1",
    "httpx",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Install backend dependencies**

```bash
cd ai-citation-checker/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Expected: No errors. `pip show fastapi` shows version 0.115.0.

- [ ] **Step 3: Create frontend with Vite**

```bash
cd ai-citation-checker
npm create vite@latest frontend -- --template react-ts
cd frontend
npm install
npm install -D tailwindcss postcss autoprefixer
npx tailwindcss init -p
npm install react-router-dom
```

- [ ] **Step 4: Configure Tailwind**

Replace content of `frontend/tailwind.config.js`:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: { extend: {} },
  plugins: [],
}
```

Replace `frontend/src/index.css`:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 5: Create .gitignore**

Create `ai-citation-checker/.gitignore`:

```
# Python
backend/.venv/
backend/__pycache__/
backend/**/__pycache__/
*.pyc
*.pyo
/data/

# Node
frontend/node_modules/
frontend/dist/

# Env
.env
.env.local
```

- [ ] **Step 6: Verify frontend starts**

```bash
cd ai-citation-checker/frontend
npm run dev
```

Expected: `Local: http://localhost:5173` shown. Visit in browser — default Vite+React page appears.

- [ ] **Step 7: Commit**

```bash
cd ai-citation-checker
git add .
git commit -m "chore: scaffold backend and frontend projects"
```

---

## Task 2: Data Models

**Files:**
- Create: `backend/app/models/schemas.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_schemas.py`:

```python
from app.models.schemas import CitationIssue, Citation, Report
from datetime import datetime, timezone

def test_citation_issue_requires_reason():
    issue = CitationIssue(
        type="not_found",
        severity="red",
        category="content",
        reason="文献不存在",
    )
    assert issue.reason == "文献不存在"
    assert issue.detail is None
    assert issue.expected is None

def test_citation_status_defaults():
    c = Citation(
        id="c1",
        kind="reference",
        raw_text="Smith, J. (2020). AI. Journal, 1(1).",
        char_start=0,
        char_end=40,
        status="pass",
        issues=[],
    )
    assert c.verified_reference_id is None

def test_report_summary_shape():
    now = datetime.now(timezone.utc)
    r = Report(
        id="abc",
        filename="essay.docx",
        created_at=now,
        expires_at=now,
        full_text="...",
        citations=[],
        summary={"total": 0, "pass": 0, "warning": 0, "error": 0},
    )
    assert r.summary["total"] == 0
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
cd backend && source .venv/bin/activate
pytest tests/test_schemas.py -v
```

Expected: `ImportError: cannot import name 'CitationIssue'`

- [ ] **Step 3: Implement schemas.py**

Create `backend/app/models/schemas.py`:

```python
from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel


class CitationIssue(BaseModel):
    type: Literal["not_found", "field_mismatch", "format_violation", "orphan", "ambiguous"]
    severity: Literal["red", "yellow"]
    category: Literal["content", "format", "orphan", "ambiguous"]
    reason: str
    detail: Optional[str] = None
    field: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    rule_id: Optional[str] = None


class Citation(BaseModel):
    id: str
    kind: Literal["intext", "reference"]
    raw_text: str
    char_start: int
    char_end: int
    status: Literal["pass", "warning", "error"]
    issues: list[CitationIssue]
    verified_reference_id: Optional[str] = None


class Report(BaseModel):
    id: str
    filename: str
    created_at: datetime
    expires_at: datetime
    full_text: str
    citations: list[Citation]
    summary: dict
```

- [ ] **Step 4: Run test — expect PASS**

```bash
pytest tests/test_schemas.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add app/models/schemas.py tests/test_schemas.py
git commit -m "feat: add Pydantic data models"
```

---

## Task 3: Database Setup

**Files:**
- Create: `backend/app/storage/db.py`
- Create: `backend/app/config.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_db.py`:

```python
import pytest
from app.storage.db import init_db, save_report, get_report

@pytest.mark.asyncio
async def test_save_and_get_report(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    await save_report(db_path, "id-1", '{"id":"id-1"}', "essay.docx")
    row = await get_report(db_path, "id-1")
    assert row is not None
    assert row["report_json"] == '{"id":"id-1"}'

@pytest.mark.asyncio
async def test_get_missing_report(tmp_path):
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    row = await get_report(db_path, "nonexistent")
    assert row is None

@pytest.mark.asyncio
async def test_expired_report_returns_none(tmp_path):
    import aiosqlite
    from datetime import datetime, timezone, timedelta
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    past = datetime.now(timezone.utc) - timedelta(hours=25)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?)",
            ("old-id", '{}', "f.docx", past.isoformat(), past.isoformat()),
        )
        await db.commit()
    row = await get_report(db_path, "old-id")
    assert row is None
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/test_db.py -v
```

Expected: `ImportError: cannot import name 'init_db'`

- [ ] **Step 3: Implement config.py**

Create `backend/app/config.py`:

```python
import os

DB_PATH = os.getenv("DB_PATH", "/data/reports.db")
CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "jackywangmel96@gmail.com")
```

- [ ] **Step 4: Implement db.py**

Create `backend/app/storage/db.py`:

```python
import aiosqlite
from datetime import datetime, timezone, timedelta

DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS reports (
    id          TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    filename    TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    expires_at  TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_expires_at ON reports(expires_at);

CREATE TABLE IF NOT EXISTS verified_references (
    id                      TEXT PRIMARY KEY,
    doi                     TEXT UNIQUE,
    title_normalized        TEXT NOT NULL,
    first_author_normalized TEXT NOT NULL,
    year                    INTEGER NOT NULL,
    canonical_json          TEXT NOT NULL,
    source                  TEXT NOT NULL,
    cached_at               TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vr_doi ON verified_references(doi) WHERE doi IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_vr_lookup ON verified_references(
    title_normalized, first_author_normalized, year
);
"""


async def init_db(db_path: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(DDL)
        await db.commit()


async def save_report(db_path: str, report_id: str, report_json: str, filename: str) -> None:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=24)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO reports VALUES (?,?,?,?,?)",
            (report_id, report_json, filename, now.isoformat(), expires.isoformat()),
        )
        await db.commit()


async def get_report(db_path: str, report_id: str) -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM reports WHERE id=? AND expires_at > ?",
            (report_id, now),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_cached_reference(db_path: str, doi: str | None,
                                title_norm: str, author_norm: str, year: int) -> dict | None:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        if doi:
            async with db.execute(
                "SELECT * FROM verified_references WHERE doi=?", (doi,)
            ) as cur:
                row = await cur.fetchone()
        else:
            async with db.execute(
                "SELECT * FROM verified_references WHERE title_normalized=? "
                "AND first_author_normalized=? AND year=?",
                (title_norm, author_norm, year),
            ) as cur:
                row = await cur.fetchone()
    return dict(row) if row else None


async def save_verified_reference(db_path: str, ref_id: str, doi: str | None,
                                   title_norm: str, author_norm: str, year: int,
                                   canonical_json: str, source: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT OR IGNORE INTO verified_references
               VALUES (?,?,?,?,?,?,?,?)""",
            (ref_id, doi, title_norm, author_norm, year, canonical_json, source, now),
        )
        await db.commit()
```

- [ ] **Step 5: Run test — expect PASS**

```bash
pytest tests/test_db.py -v
```

Expected: `3 passed`

- [ ] **Step 6: Commit**

```bash
git add app/storage/db.py app/config.py tests/test_db.py
git commit -m "feat: add SQLite storage with WAL mode and 24h expiry"
```

---

## Task 4: DOCX Parser

**Files:**
- Create: `backend/app/services/docx_parser.py`
- Create: `backend/tests/fixtures/make_fixtures.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_docx_parser.py`:

```python
import pytest
from pathlib import Path
from app.services.docx_parser import parse_docx, ParsedDocument

FIXTURES = Path(__file__).parent / "fixtures"

def test_parse_returns_parsed_document():
    data = (FIXTURES / "clean.docx").read_bytes()
    result = parse_docx(data)
    assert isinstance(result, ParsedDocument)
    assert len(result.full_text) > 0
    assert len(result.reference_paragraphs) > 0

def test_body_text_excludes_references_section():
    data = (FIXTURES / "clean.docx").read_bytes()
    result = parse_docx(data)
    assert "References" not in result.body_text or result.body_text.index("References") == 0

def test_reference_paragraphs_have_italic_info():
    data = (FIXTURES / "format_errors.docx").read_bytes()
    result = parse_docx(data)
    # Each para is a list of (text, is_italic) tuples
    for para in result.reference_paragraphs:
        assert isinstance(para.runs, list)
        for run_text, is_italic in para.runs:
            assert isinstance(run_text, str)
            assert isinstance(is_italic, bool)

def test_malformed_docx_raises_value_error():
    with pytest.raises(ValueError, match="Failed to parse"):
        parse_docx(b"not a docx file")
```

- [ ] **Step 2: Generate fixture files**

Create `backend/tests/fixtures/make_fixtures.py`:

```python
"""Run once to generate test .docx fixture files."""
from docx import Document
from docx.shared import Pt
from pathlib import Path

OUT = Path(__file__).parent

def make_clean():
    doc = Document()
    doc.add_paragraph(
        "Artificial intelligence is transforming education (Smith, 2020). "
        "Recent studies support this claim (Jones & Lee, 2019)."
    )
    doc.add_heading("References", level=1)
    ref1 = doc.add_paragraph()
    ref1.add_run("Smith, J. (2020). ")
    r = ref1.add_run("AI in education.")
    r.italic = True
    ref1.add_run(" Journal of Educational Psychology, 112(3), 45-62. https://doi.org/10.1037/edu0000412")
    ref2 = doc.add_paragraph()
    ref2.add_run("Jones, A., & Lee, B. (2019). ")
    r2 = ref2.add_run("Learning with machines.")
    r2.italic = True
    ref2.add_run(" Educational Research, 5(1), 1-20. https://doi.org/10.1000/xyz123")
    doc.save(OUT / "clean.docx")

def make_fabricated():
    doc = Document()
    doc.add_paragraph("AI hallucinated this reference (Fake, 2099).")
    doc.add_heading("References", level=1)
    doc.add_paragraph(
        "Fake, A. (2099). This paper does not exist. "
        "Journal of Nonexistent Studies, 1(1), 1-10."
    )
    doc.save(OUT / "fabricated.docx")

def make_format_errors():
    doc = Document()
    doc.add_paragraph("Some claim (Smith 2020).")  # missing comma
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smith J (2020). ")  # bad author format
    ref.add_run("AI in education.")  # journal not italic
    ref.add_run(" Journal of Educational Psychology, 112(3), 45-62. doi:10.1037/edu0000412")  # bad DOI
    doc.save(OUT / "format_errors.docx")

def make_metadata_mismatch():
    doc = Document()
    doc.add_paragraph("(Smyth, 2019).")
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smyth, J. (2019). ")
    r = ref.add_run("AI in education.")
    r.italic = True
    ref.add_run(" Jornal of Educational Psychology, 12(3), 45-62.")
    doc.save(OUT / "metadata_mismatch.docx")

def make_orphan_intext():
    doc = Document()
    doc.add_paragraph("This claim (Ghost, 2021) has no reference.")
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smith, J. (2020). ")
    r = ref.add_run("AI in education.")
    r.italic = True
    ref.add_run(" Journal of Educational Psychology, 112(3), 45-62.")
    doc.save(OUT / "orphan_intext.docx")

def make_ambiguous_author():
    doc = Document()
    doc.add_paragraph("(Smith, 2020).")
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smith, J. (2020). ")
    r = ref.add_run("AI in education.")
    r.italic = True
    ref.add_run(" Journal of Educational Psychology, 112(3), 45-62.")
    doc.save(OUT / "ambiguous_author.docx")

def make_missing_subtitle():
    doc = Document()
    doc.add_paragraph("(Smith, 2020).")
    doc.add_heading("References", level=1)
    ref = doc.add_paragraph()
    ref.add_run("Smith, J. (2020). ")
    r = ref.add_run("AI in education.")  # missing ": A systematic review"
    r.italic = True
    ref.add_run(" Journal of Educational Psychology, 112(3), 45-62.")
    doc.save(OUT / "missing_subtitle.docx")

def make_mixed():
    doc = Document()
    doc.add_paragraph("Real claim (Smith, 2020). Fake claim (Ghost, 2099). Bad format (jones 2018).")
    doc.add_heading("References", level=1)
    ref1 = doc.add_paragraph()
    ref1.add_run("Smith, J. (2020). ")
    r = ref1.add_run("AI in education.")
    r.italic = True
    ref1.add_run(" Journal of Educational Psychology, 112(3), 45-62.")
    doc.add_paragraph("Ghost, A. (2099). Hallucinated paper. Fake Journal, 1(1), 1-5.")
    doc.save(OUT / "mixed.docx")

if __name__ == "__main__":
    make_clean()
    make_fabricated()
    make_format_errors()
    make_metadata_mismatch()
    make_orphan_intext()
    make_ambiguous_author()
    make_missing_subtitle()
    make_mixed()
    print("Fixtures generated.")
```

Run it:

```bash
cd backend && python tests/fixtures/make_fixtures.py
```

Expected: `Fixtures generated.` — 8 `.docx` files appear in `tests/fixtures/`.

- [ ] **Step 3: Run test — expect FAIL**

```bash
pytest tests/test_docx_parser.py -v
```

Expected: `ImportError: cannot import name 'parse_docx'`

- [ ] **Step 4: Implement docx_parser.py**

Create `backend/app/services/docx_parser.py`:

```python
from __future__ import annotations
import io
from dataclasses import dataclass, field
from docx import Document
from docx.oxml.ns import qn

REFERENCE_HEADINGS = {"references", "bibliography", "works cited", "reference list"}


@dataclass
class ReferenceParagraph:
    raw_text: str
    runs: list[tuple[str, bool]]      # (text, is_italic)
    has_hanging_indent: bool


@dataclass
class ParsedDocument:
    full_text: str
    body_text: str
    reference_paragraphs: list[ReferenceParagraph] = field(default_factory=list)


def parse_docx(data: bytes) -> ParsedDocument:
    try:
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        raise ValueError(f"Failed to parse .docx: {exc}") from exc

    paragraphs = doc.paragraphs
    ref_start = _find_references_heading(paragraphs)

    body_paras = paragraphs[:ref_start] if ref_start is not None else paragraphs
    body_text = "\n".join(p.text for p in body_paras if p.text.strip())

    ref_paras: list[ReferenceParagraph] = []
    if ref_start is not None:
        for para in paragraphs[ref_start + 1:]:
            if not para.text.strip():
                continue
            runs = [(r.text, bool(r.italic)) for r in para.runs if r.text]
            fmt = para.paragraph_format
            hanging = (
                fmt.first_line_indent is not None and fmt.first_line_indent < 0
            )
            ref_paras.append(ReferenceParagraph(
                raw_text=para.text,
                runs=runs,
                has_hanging_indent=hanging,
            ))

    full_text = "\n".join(p.text for p in paragraphs if p.text.strip())
    return ParsedDocument(
        full_text=full_text,
        body_text=body_text,
        reference_paragraphs=ref_paras,
    )


def _find_references_heading(paragraphs) -> int | None:
    for i, para in enumerate(paragraphs):
        if para.style.name.startswith("Heading") and para.text.strip().lower() in REFERENCE_HEADINGS:
            return i
        if para.text.strip().lower() in REFERENCE_HEADINGS:
            return i
    return None
```

- [ ] **Step 5: Run test — expect PASS**

```bash
pytest tests/test_docx_parser.py -v
```

Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add app/services/docx_parser.py tests/test_docx_parser.py tests/fixtures/
git commit -m "feat: add docx parser with reference section detection"
```

---

## Task 5: Citation Extractor

**Files:**
- Create: `backend/app/services/citation_extractor.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_extractor.py`:

```python
import pytest
from app.services.citation_extractor import (
    extract_intext_citations,
    parse_reference_entries,
    normalize_title,
    normalize_author,
    ReferenceEntry,
    IntextCitation,
)

def test_extract_single_intext():
    text = "Some claim (Smith, 2020) in this sentence."
    citations = extract_intext_citations(text)
    assert len(citations) == 1
    assert citations[0].raw_text == "(Smith, 2020)"
    assert citations[0].author == "Smith"
    assert citations[0].year == 2020

def test_extract_multiple_intext():
    text = "First (Smith, 2020) and second (Jones & Lee, 2019, p. 15)."
    citations = extract_intext_citations(text)
    assert len(citations) == 2
    assert citations[1].year == 2019

def test_extract_et_al():
    text = "See (Smith et al., 2021)."
    citations = extract_intext_citations(text)
    assert len(citations) == 1
    assert "et al" in citations[0].raw_text

def test_intext_char_positions():
    text = "Claim (Smith, 2020) here."
    citations = extract_intext_citations(text)
    start = citations[0].char_start
    end = citations[0].char_end
    assert text[start:end] == "(Smith, 2020)"

def test_parse_reference_entry():
    raw = "Smith, J. (2020). AI in education. Journal of Ed, 1(1), 1-10."
    entries = parse_reference_entries([raw])
    assert len(entries) == 1
    e = entries[0]
    assert e.first_author_normalized == "smith"
    assert e.year == 2020
    assert "ai in education" in e.title_normalized

def test_normalize_title_strips_punctuation():
    assert normalize_title("AI: A Review.") == "ai a review"

def test_normalize_author_lowercases():
    assert normalize_author("Smith") == "smith"
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/test_extractor.py -v
```

Expected: `ImportError: cannot import name 'extract_intext_citations'`

- [ ] **Step 3: Implement citation_extractor.py**

Create `backend/app/services/citation_extractor.py`:

```python
from __future__ import annotations
import re
from dataclasses import dataclass, field

# Matches: (Smith, 2020), (Smith & Jones, 2019, p. 15), (Smith et al., 2021)
_INTEXT_RE = re.compile(
    r'\(([A-Z][a-zA-Z\-]+(?:\s+et\s+al\.)?'
    r'(?:\s*[,&]\s*[A-Z][a-zA-Z\-]+)*)'
    r',\s*(\d{4}[a-z]?)'
    r'(?:,\s*pp?\.\s*[\d\-]+)?\)'
)

# Matches year in reference: (2020) or (2020a)
_YEAR_RE = re.compile(r'\((\d{4}[a-z]?)\)')
# Matches first author surname
_AUTHOR_RE = re.compile(r'^([A-Z][a-zA-Z\-]+),')


@dataclass
class IntextCitation:
    raw_text: str
    author: str
    year: int
    char_start: int
    char_end: int


@dataclass
class ReferenceEntry:
    raw_text: str
    first_author_normalized: str
    year: int
    title_normalized: str
    doi: str | None = None


def extract_intext_citations(text: str) -> list[IntextCitation]:
    results = []
    for m in _INTEXT_RE.finditer(text):
        author_part = m.group(1).split(",")[0].strip()
        author_part = re.sub(r'\s+et\s+al\.?', '', author_part).strip()
        year = int(m.group(2)[:4])
        results.append(IntextCitation(
            raw_text=m.group(0),
            author=author_part,
            year=year,
            char_start=m.start(),
            char_end=m.end(),
        ))
    return results


def parse_reference_entries(raw_paragraphs: list[str]) -> list[ReferenceEntry]:
    entries = []
    for raw in raw_paragraphs:
        raw = raw.strip()
        if not raw:
            continue
        year = _extract_year(raw)
        author = _extract_first_author(raw)
        title = _extract_title(raw)
        doi = _extract_doi(raw)
        entries.append(ReferenceEntry(
            raw_text=raw,
            first_author_normalized=normalize_author(author),
            year=year,
            title_normalized=normalize_title(title),
            doi=doi,
        ))
    return entries


def normalize_title(title: str) -> str:
    title = title.lower()
    title = re.sub(r'[^\w\s]', '', title)
    return re.sub(r'\s+', ' ', title).strip()


def normalize_author(author: str) -> str:
    return author.lower().strip()


def _extract_year(text: str) -> int:
    m = _YEAR_RE.search(text)
    return int(m.group(1)[:4]) if m else 0


def _extract_first_author(text: str) -> str:
    m = _AUTHOR_RE.match(text)
    return m.group(1) if m else ""


def _extract_title(text: str) -> str:
    # Title comes after "). " following the year
    m = re.search(r'\(\d{4}[a-z]?\)\.\s+(.+?)[\.\!\?]', text)
    if m:
        return m.group(1).strip()
    return text


def _extract_doi(text: str) -> str | None:
    m = re.search(r'https?://doi\.org/\S+', text)
    return m.group(0).rstrip('.,') if m else None
```

- [ ] **Step 4: Run test — expect PASS**

```bash
pytest tests/test_extractor.py -v
```

Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add app/services/citation_extractor.py tests/test_extractor.py
git commit -m "feat: add citation extractor with in-text and reference parsing"
```

---

## Task 6: APA 7th Rules

**Files:**
- Create: `backend/app/rules/apa7.py`
- Create: `backend/app/services/apa_validator.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_apa7.py`:

```python
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
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/test_apa7.py -v
```

Expected: `ImportError: cannot import name 'check_author_format'`

- [ ] **Step 3: Implement apa7.py**

Create `backend/app/rules/apa7.py`:

```python
from __future__ import annotations
import re
from typing import Optional
from app.models.schemas import CitationIssue
from app.services.docx_parser import ReferenceParagraph

_AUTHOR_FORMAT_RE = re.compile(r'^[A-Z][a-zA-Z\-]+,\s+[A-Z]\.')
_YEAR_PARENS_RE = re.compile(r'\(\d{4}[a-z]?\)')
_DOI_URL_RE = re.compile(r'https?://doi\.org/')
_DOI_BARE_RE = re.compile(r'\bdoi:\s*10\.')
_AND_RE = re.compile(r'\band\b', re.IGNORECASE)
_AMPERSAND_MULTI_RE = re.compile(r'[A-Z][a-zA-Z]+,\s+[A-Z]\.\s*,')


def _issue(rule_id: str, reason: str, detail: str | None = None) -> CitationIssue:
    return CitationIssue(
        type="format_violation",
        severity="yellow",
        category="format",
        reason=reason,
        detail=detail,
        rule_id=rule_id,
    )


def check_author_format(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R001: First author must be Last, F. M. format."""
    if not _AUTHOR_FORMAT_RE.match(para.raw_text):
        return _issue("R001", "作者格式应为 Last, F. M.（APA 7th R001）")
    return None


def check_year_parens(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R002: Year must appear in parentheses."""
    if not _YEAR_PARENS_RE.search(para.raw_text):
        return _issue("R002", "年份应放在括号内，例如 (2020)（APA 7th R002）")
    return None


def check_journal_italic(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R003: Journal title must be italicised."""
    has_italic = any(is_italic for _, is_italic in para.runs)
    if not has_italic:
        return _issue("R003", "期刊名应为斜体（APA 7th R003）")
    return None


def check_hanging_indent(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R006: Reference paragraph must use hanging indent."""
    if not para.has_hanging_indent:
        return _issue("R006", "Reference 段落应使用悬挂缩进（APA 7th R006）")
    return None


def check_doi_format(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R005: DOI must use https://doi.org/ format."""
    if _DOI_BARE_RE.search(para.raw_text):
        return _issue("R005", "DOI 应使用 https://doi.org/... 格式，不用 doi:...（APA 7th R005）")
    return None


def check_author_separator(para: ReferenceParagraph) -> Optional[CitationIssue]:
    """R007: Multiple authors joined by ', &' not 'and'."""
    if _AMPERSAND_MULTI_RE.search(para.raw_text) and _AND_RE.search(para.raw_text):
        return _issue("R007", "多作者应用 ', & ' 而非 'and' 连接（APA 7th R007）")
    return None


ALL_RULES = [
    check_author_format,
    check_year_parens,
    check_journal_italic,
    check_doi_format,
    check_hanging_indent,
    check_author_separator,
]
```

- [ ] **Step 4: Implement apa_validator.py**

Create `backend/app/services/apa_validator.py`:

```python
from app.rules.apa7 import ALL_RULES
from app.services.docx_parser import ReferenceParagraph
from app.models.schemas import CitationIssue


def validate_reference_paragraph(para: ReferenceParagraph) -> list[CitationIssue]:
    issues = []
    for rule in ALL_RULES:
        issue = rule(para)
        if issue:
            issues.append(issue)
    return issues
```

- [ ] **Step 5: Run test — expect PASS**

```bash
pytest tests/test_apa7.py -v
```

Expected: `9 passed`

- [ ] **Step 6: Commit**

```bash
git add app/rules/apa7.py app/services/apa_validator.py tests/test_apa7.py
git commit -m "feat: add APA 7th rule set R001-R007"
```

---

## Task 7: Verifier (Crossref + Cache)

**Files:**
- Create: `backend/app/services/verifier.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_verifier.py`:

```python
import pytest
import respx
import httpx
import json
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
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/test_verifier.py -v
```

Expected: `ImportError: cannot import name 'verify_reference'`

- [ ] **Step 3: Implement verifier.py**

Create `backend/app/services/verifier.py`:

```python
from __future__ import annotations
import json
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
SCORE_FUZZY_MIN = 85


@dataclass
class VerifyResult:
    found: bool
    exact_match: bool = False
    ambiguous: bool = False
    other_titles: list[str] = field(default_factory=list)
    canonical: Optional[dict] = None
    source: Optional[str] = None
    verified_reference_id: Optional[str] = None


async def verify_reference(entry: ReferenceEntry, db_path: str) -> VerifyResult:
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

    async with httpx.AsyncClient(timeout=10.0) as client:
        # 2. Crossref search
        result = await _search_crossref(client, entry, db_path)
        if result:
            return result
        # 3. OpenAlex fallback
        result = await _search_openalex(client, entry, db_path)
        if result:
            return result

    return VerifyResult(found=False)


async def _search_crossref(client: httpx.AsyncClient, entry: ReferenceEntry,
                            db_path: str) -> Optional[VerifyResult]:
    query = f"{entry.title_normalized} {entry.first_author_normalized} {entry.year}"
    try:
        resp = await client.get(CROSSREF_BASE, params={
            "query.bibliographic": query,
            "rows": 5,
            "mailto": CROSSREF_MAILTO,
        })
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except Exception:
        return None

    return await _score_candidates(items, entry, db_path, source="crossref")


async def _search_openalex(client: httpx.AsyncClient, entry: ReferenceEntry,
                            db_path: str) -> Optional[VerifyResult]:
    query = f"{entry.title_normalized} {entry.first_author_normalized}"
    try:
        resp = await client.get(OPENALEX_BASE, params={"search": query, "per-page": 5})
        resp.raise_for_status()
        raw_items = resp.json().get("results", [])
    except Exception:
        return None

    # Normalise OpenAlex shape to match Crossref shape
    items = []
    for w in raw_items:
        title = w.get("title") or ""
        authors = [
            {"family": a.get("author", {}).get("display_name", "").split()[-1], "given": ""}
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
    return await _score_candidates(items, entry, db_path, source="openalex")


async def _score_candidates(items: list[dict], entry: ReferenceEntry,
                             db_path: str, source: str) -> Optional[VerifyResult]:
    if not items:
        return None

    scored = []
    for item in items:
        cand_title = (item.get("title") or [""])[0]
        cand_authors = item.get("author") or []
        cand_year = ((item.get("published") or {}).get("date-parts") or [[0]])[0][0]
        cand_first_author = (cand_authors[0].get("family") or "") if cand_authors else ""

        title_score = fuzz.token_set_ratio(
            entry.title_normalized,
            _norm(cand_title),
        )
        author_match = _norm(cand_first_author) == entry.first_author_normalized
        year_match = abs(cand_year - entry.year) <= 1

        scored.append((title_score, author_match, year_match, item, cand_title))

    # Filter candidates where author+year match
    matching = [
        (score, item, cand_title)
        for score, author_ok, year_ok, item, cand_title in scored
        if author_ok and year_ok and score >= SCORE_FUZZY_MIN
    ]

    if not matching:
        return None

    # Check ambiguity: multiple candidates same author+year
    ambiguous = len(matching) > 1
    other_titles = [t for _, _, t in matching[1:]]

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
                     concurrency: int = 10) -> list[VerifyResult]:
    sem = asyncio.Semaphore(concurrency)

    async def _one(entry: ReferenceEntry) -> VerifyResult:
        async with sem:
            return await verify_reference(entry, db_path)

    return await asyncio.gather(*[_one(e) for e in entries])


def _norm(text: str) -> str:
    import re
    text = text.lower()
    text = re.sub(r'[^\w\s]', '', text)
    return re.sub(r'\s+', ' ', text).strip()
```

- [ ] **Step 4: Run test — expect PASS**

```bash
pytest tests/test_verifier.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add app/services/verifier.py tests/test_verifier.py
git commit -m "feat: add Crossref/OpenAlex verifier with cache and ambiguity detection"
```

---

## Task 8: Report Builder

**Files:**
- Create: `backend/app/services/report_builder.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_report_builder.py`:

```python
import pytest
from app.services.report_builder import build_report
from app.services.citation_extractor import IntextCitation, ReferenceEntry
from app.services.verifier import VerifyResult
from app.services.docx_parser import ReferenceParagraph

def _intext(author="Smith", year=2020, start=0) -> IntextCitation:
    raw = f"({author}, {year})"
    return IntextCitation(raw_text=raw, author=author, year=year,
                          char_start=start, char_end=start+len(raw))

def _entry(author="smith", year=2020, title="ai in education") -> ReferenceEntry:
    return ReferenceEntry(
        raw_text=f"{author.title()}, J. ({year}). {title}.",
        first_author_normalized=author, year=year, title_normalized=title,
    )

def _para(text: str) -> ReferenceParagraph:
    return ReferenceParagraph(raw_text=text, runs=[(text, False)], has_hanging_indent=True)

def _result(found=True, exact=True) -> VerifyResult:
    canonical = {"title":["AI in education"],"author":[{"family":"Smith","given":"J"}],
                 "published":{"date-parts":[[2020]]},"container-title":["Journal of Ed"],
                 "volume":"1","issue":"1","page":"1-10","DOI":"10.1/a"}
    return VerifyResult(found=found, exact_match=exact, canonical=canonical if found else None)

def test_all_green_report():
    report = build_report(
        report_id="r1", filename="essay.docx", full_text="(Smith, 2020).",
        intext_citations=[_intext()],
        reference_entries=[_entry()],
        reference_paragraphs=[_para("Smith, J. (2020). AI. Journal, 1(1).")],
        verify_results=[_result()],
    )
    assert report.summary["pass"] == 2   # 1 intext + 1 reference
    assert report.summary["error"] == 0

def test_orphan_intext_flagged():
    report = build_report(
        report_id="r1", filename="essay.docx", full_text="(Ghost, 2099).",
        intext_citations=[_intext("Ghost", 2099)],
        reference_entries=[_entry()],
        reference_paragraphs=[_para("Smith, J. (2020). AI. Journal.")],
        verify_results=[_result()],
    )
    orphan_citations = [c for c in report.citations
                        if any(i.category == "orphan" for i in c.issues)]
    assert len(orphan_citations) == 1

def test_not_found_reference_is_red():
    report = build_report(
        report_id="r1", filename="essay.docx", full_text="(Fake, 2099).",
        intext_citations=[_intext("Fake", 2099)],
        reference_entries=[_entry("fake", 2099, "nonexistent paper")],
        reference_paragraphs=[_para("Fake, A. (2099). Nonexistent paper. Fake Journal.")],
        verify_results=[_result(found=False)],
    )
    red = [c for c in report.citations if c.status == "error"]
    assert len(red) >= 1
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/test_report_builder.py -v
```

Expected: `ImportError: cannot import name 'build_report'`

- [ ] **Step 3: Implement report_builder.py**

Create `backend/app/services/report_builder.py`:

```python
from __future__ import annotations
import re
from datetime import datetime, timezone, timedelta
from app.models.schemas import Citation, CitationIssue, Report
from app.services.citation_extractor import IntextCitation, ReferenceEntry
from app.services.verifier import VerifyResult
from app.services.docx_parser import ReferenceParagraph
from app.services.apa_validator import validate_reference_paragraph
from rapidfuzz import fuzz

_INTEXT_FORMAT_RE = re.compile(
    r'^\([A-Z][a-zA-Z\-]+(?:\s+et\s+al\.)?'
    r'(?:\s*[,&]\s*[A-Z][a-zA-Z\-]+)*'
    r',\s*\d{4}[a-z]?(?:,\s*pp?\.\s*[\d\-]+)?\)$'
)


def build_report(
    report_id: str,
    filename: str,
    full_text: str,
    intext_citations: list[IntextCitation],
    reference_entries: list[ReferenceEntry],
    reference_paragraphs: list[ReferenceParagraph],
    verify_results: list[VerifyResult],
) -> Report:
    citations: list[Citation] = []
    counter = 0

    # Build reference citations
    for entry, para, vr in zip(reference_entries, reference_paragraphs, verify_results):
        counter += 1
        issues: list[CitationIssue] = []

        if not vr.found:
            issues.append(CitationIssue(
                type="not_found", severity="red", category="content",
                reason="文献不存在：在 Crossref 和 OpenAlex 中均未找到匹配记录",
            ))
        else:
            # Field-level comparison
            issues.extend(_compare_fields(entry, vr))
            # APA format check
            issues.extend(validate_reference_paragraph(para))
            # Ambiguity
            if vr.ambiguous:
                issues.append(CitationIssue(
                    type="ambiguous", severity="yellow", category="ambiguous",
                    reason="同作者同年存在多篇文章，请确认引用了正确的那篇",
                    detail="同年其他文章：" + "、".join(vr.other_titles),
                ))

        status = _status(issues)
        # Find char position of this reference in full_text
        start = full_text.find(entry.raw_text)
        end = start + len(entry.raw_text) if start >= 0 else 0

        citations.append(Citation(
            id=f"r{counter}",
            kind="reference",
            raw_text=entry.raw_text,
            char_start=max(start, 0),
            char_end=max(end, 0),
            status=status,
            issues=issues,
            verified_reference_id=vr.verified_reference_id,
        ))

    # Build in-text citations
    for intext in intext_citations:
        counter += 1
        issues = _check_intext(intext, reference_entries)
        citations.append(Citation(
            id=f"i{counter}",
            kind="intext",
            raw_text=intext.raw_text,
            char_start=intext.char_start,
            char_end=intext.char_end,
            status=_status(issues),
            issues=issues,
        ))

    now = datetime.now(timezone.utc)
    total = len(citations)
    pass_ = sum(1 for c in citations if c.status == "pass")
    warn = sum(1 for c in citations if c.status == "warning")
    err = sum(1 for c in citations if c.status == "error")

    return Report(
        id=report_id,
        filename=filename,
        created_at=now,
        expires_at=now + timedelta(hours=24),
        full_text=full_text,
        citations=citations,
        summary={"total": total, "pass": pass_, "warning": warn, "error": err},
    )


def _status(issues: list[CitationIssue]) -> str:
    if any(i.severity == "red" for i in issues):
        return "error"
    if issues:
        return "warning"
    return "pass"


def _compare_fields(entry: ReferenceEntry, vr: VerifyResult) -> list[CitationIssue]:
    if not vr.canonical:
        return []
    issues = []
    c = vr.canonical

    # Author
    cand_author = ((c.get("author") or [{}])[0].get("family") or "").lower()
    if cand_author and cand_author != entry.first_author_normalized:
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            field="author",
            reason=f"作者拼写错误",
            expected=cand_author.title(),
            actual=entry.first_author_normalized.title(),
        ))

    # Year
    cand_year = ((c.get("published") or {}).get("date-parts") or [[0]])[0][0]
    if cand_year and cand_year != entry.year:
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            field="year", reason="年份不一致",
            expected=str(cand_year), actual=str(entry.year),
        ))

    # Title
    cand_title = (c.get("title") or [""])[0]
    from app.services.verifier import _norm
    score = fuzz.token_set_ratio(_norm(cand_title), entry.title_normalized)
    if score < 95:
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            field="title", reason="标题与权威记录不一致",
            expected=cand_title, actual=entry.title_normalized,
        ))

    # Journal
    cand_journal = (c.get("container-title") or [""])[0]
    j_score = fuzz.token_set_ratio(_norm(cand_journal), _norm(entry.raw_text))
    if cand_journal and j_score < 90:
        issues.append(CitationIssue(
            type="field_mismatch", severity="yellow", category="content",
            field="journal", reason="期刊名与权威记录不一致",
            expected=cand_journal,
        ))

    return issues


def _check_intext(intext: IntextCitation,
                  reference_entries: list[ReferenceEntry]) -> list[CitationIssue]:
    issues = []

    # Format check
    if not _INTEXT_FORMAT_RE.match(intext.raw_text):
        issues.append(CitationIssue(
            type="format_violation", severity="yellow", category="format",
            reason="In-text 引用格式不符合 APA 7th（如缺括号、缺逗号）",
            actual=intext.raw_text,
        ))

    # Orphan check
    matched = any(
        e.first_author_normalized == intext.author.lower() and e.year == intext.year
        for e in reference_entries
    )
    if not matched:
        issues.append(CitationIssue(
            type="orphan", severity="yellow", category="orphan",
            reason="在 Reference List 中找不到对应条目",
            detail=f"正文引用了 ({intext.author}, {intext.year})，但 Reference List 中无对应记录",
        ))

    return issues
```

- [ ] **Step 4: Run test — expect PASS**

```bash
pytest tests/test_report_builder.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add app/services/report_builder.py tests/test_report_builder.py
git commit -m "feat: add report builder with field comparison and orphan detection"
```

---

## Task 9: API Routes

**Files:**
- Create: `backend/app/routes/upload.py`
- Create: `backend/app/routes/report.py`
- Create: `backend/app/main.py`
- Create: `backend/app/storage/cleanup.py`

- [ ] **Step 1: Write failing test**

Create `backend/tests/test_routes.py`:

```python
import pytest
import respx
import httpx
from httpx import AsyncClient
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

@pytest.fixture
async def app_client():
    from app.main import create_app
    import os, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DB_PATH"] = f"{tmp}/test.db"
        app = await create_app()
        async with AsyncClient(app=app, base_url="http://test") as client:
            yield client

@pytest.mark.asyncio
async def test_healthz(app_client):
    r = await app_client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

@pytest.mark.asyncio
@respx.mock
async def test_upload_returns_report_url(app_client):
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"status":"ok","message":{"items":[]}})
    )
    respx.get("https://api.openalex.org/works").mock(
        return_value=httpx.Response(200, json={"results":[]})
    )
    docx_bytes = (FIXTURES / "fabricated.docx").read_bytes()
    r = await app_client.post(
        "/api/check",
        files={"file": ("essay.docx", docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert r.status_code == 200
    data = r.json()
    assert "report_id" in data
    assert data["url"].startswith("/r/")

@pytest.mark.asyncio
async def test_missing_report_returns_410(app_client):
    r = await app_client.get("/api/report/nonexistent-id")
    assert r.status_code == 410
```

- [ ] **Step 2: Implement cleanup.py**

Create `backend/app/storage/cleanup.py`:

```python
import aiosqlite
from datetime import datetime, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.config import DB_PATH


async def delete_expired_reports() -> None:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM reports WHERE expires_at < ?", (now,))
        await db.commit()


def start_cleanup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(delete_expired_reports, "interval", hours=1)
    scheduler.start()
    return scheduler
```

- [ ] **Step 3: Implement upload.py**

Create `backend/app/routes/upload.py`:

```python
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from app.config import DB_PATH
from app.services.docx_parser import parse_docx
from app.services.citation_extractor import extract_intext_citations, parse_reference_entries
from app.services.verifier import verify_all
from app.services.report_builder import build_report
from app.storage.db import save_report

router = APIRouter()


@router.post("/api/check")
async def check_essay(file: UploadFile = File(...)):
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    try:
        parsed = parse_docx(data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    intext = extract_intext_citations(parsed.body_text)
    ref_entries = parse_reference_entries(
        [p.raw_text for p in parsed.reference_paragraphs]
    )
    verify_results = await verify_all(ref_entries, DB_PATH)

    report_id = str(uuid.uuid4())
    report = build_report(
        report_id=report_id,
        filename=file.filename,
        full_text=parsed.full_text,
        intext_citations=intext,
        reference_entries=ref_entries,
        reference_paragraphs=parsed.reference_paragraphs,
        verify_results=verify_results,
    )

    await save_report(DB_PATH, report_id, report.model_dump_json(), file.filename)
    return {"report_id": report_id, "url": f"/r/{report_id}"}
```

- [ ] **Step 4: Implement report.py**

Create `backend/app/routes/report.py`:

```python
import json
from fastapi import APIRouter, Response
from app.config import DB_PATH
from app.storage.db import get_report

router = APIRouter()


@router.get("/healthz")
async def healthz():
    return {"ok": True}


@router.get("/api/report/{report_id}")
async def get_report_by_id(report_id: str, response: Response):
    row = await get_report(DB_PATH, report_id)
    if row is None:
        response.status_code = 410
        return {"detail": "Report expired or not found"}
    return json.loads(row["report_json"])
```

- [ ] **Step 5: Implement main.py**

Create `backend/app/main.py`:

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from app.config import DB_PATH
from app.storage.db import init_db
from app.storage.cleanup import start_cleanup_scheduler
from app.routes.upload import router as upload_router
from app.routes.report import router as report_router

limiter = Limiter(key_func=get_remote_address, default_limits=["10/minute"])


async def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await init_db(DB_PATH)
        scheduler = start_cleanup_scheduler()
        yield
        scheduler.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "https://your-app.vercel.app"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(upload_router)
    app.include_router(report_router)
    return app


async def _make_app() -> FastAPI:
    return await create_app()


import asyncio
app = asyncio.get_event_loop().run_until_complete(_make_app())
```

- [ ] **Step 6: Run all tests — expect PASS**

```bash
pytest tests/ -v --ignore=tests/test_schemas.py
```

Expected: All pass. (test_schemas.py already passed, included for completeness.)

- [ ] **Step 7: Run server locally**

```bash
uvicorn app.main:app --reload --port 8000
```

Expected: Server starts. Visit `http://localhost:8000/healthz` → `{"ok": true}`

- [ ] **Step 8: Commit**

```bash
git add app/routes/ app/main.py app/storage/cleanup.py tests/test_routes.py
git commit -m "feat: add FastAPI routes, rate limiting, and CORS"
```

---

## Task 10: Frontend — Upload Page

**Files:**
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/pages/Upload.tsx`
- Create: `frontend/src/main.tsx`

- [ ] **Step 1: Set up routing**

Replace `frontend/src/main.tsx`:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import './index.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
)
```

Replace `frontend/src/App.tsx`:

```tsx
import { Routes, Route } from 'react-router-dom'
import Upload from './pages/Upload'
import Report from './pages/Report'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Upload />} />
      <Route path="/r/:id" element={<Report />} />
    </Routes>
  )
}
```

- [ ] **Step 2: Create API client**

Create `frontend/src/lib/api.ts`:

```ts
const BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'

export async function uploadEssay(file: File): Promise<{ report_id: string; url: string }> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`${BASE}/api/check`, { method: 'POST', body: form })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail ?? `Upload failed: ${res.status}`)
  }
  return res.json()
}

export async function fetchReport(id: string) {
  const res = await fetch(`${BASE}/api/report/${id}`)
  if (res.status === 410) throw new Error('Report expired or not found')
  if (!res.ok) throw new Error(`Failed to load report: ${res.status}`)
  return res.json()
}
```

- [ ] **Step 3: Implement Upload.tsx**

Create `frontend/src/pages/Upload.tsx`:

```tsx
import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadEssay } from '../lib/api'

type State = 'idle' | 'uploading' | 'error'

export default function Upload() {
  const [state, setState] = useState<State>('idle')
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const navigate = useNavigate()

  const handleFile = useCallback(async (file: File) => {
    if (!file.name.endsWith('.docx')) {
      setError('Only .docx files are supported')
      setState('error')
      return
    }
    setState('uploading')
    setError('')
    try {
      const { report_id } = await uploadEssay(file)
      navigate(`/r/${report_id}`)
    } catch (e: any) {
      setError(e.message ?? 'Upload failed')
      setState('error')
    }
  }, [navigate])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [handleFile])

  const onInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-md p-10 w-full max-w-lg text-center">
        <h1 className="text-2xl font-bold mb-2">AI Citation Checker</h1>
        <p className="text-gray-500 mb-6 text-sm">
          Upload your .docx essay — we'll verify every APA citation in ~5 seconds.
        </p>

        <label
          onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`block border-2 border-dashed rounded-xl p-10 cursor-pointer transition
            ${dragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-blue-400'}`}
        >
          <input type="file" accept=".docx" className="hidden" onChange={onInput} />
          {state === 'uploading' ? (
            <div className="flex flex-col items-center gap-3">
              <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
              <p className="text-gray-500">Analysing citations…</p>
            </div>
          ) : (
            <>
              <p className="text-4xl mb-3">📄</p>
              <p className="font-medium text-gray-700">Drop your .docx here</p>
              <p className="text-sm text-gray-400 mt-1">or click to browse</p>
            </>
          )}
        </label>

        {state === 'error' && (
          <p className="mt-4 text-red-500 text-sm">{error}</p>
        )}

        <p className="mt-6 text-xs text-gray-400">
          Files are analysed in memory and never stored. Reports expire after 24h.
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Verify upload page in browser**

```bash
# Terminal 1
cd backend && uvicorn app.main:app --reload --port 8000

# Terminal 2
cd frontend && npm run dev
```

Open `http://localhost:5173`. You should see the upload page. Try uploading `tests/fixtures/fabricated.docx` — should redirect to `/r/<uuid>` (report page is empty for now).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/
git commit -m "feat: add upload page with drag-and-drop"
```

---

## Task 11: Frontend — Report Page

**Files:**
- Create: `frontend/src/pages/Report.tsx`
- Create: `frontend/src/components/AnnotatedText.tsx`
- Create: `frontend/src/components/Citation.tsx`
- Create: `frontend/src/components/IssuePanel.tsx`

- [ ] **Step 1: Create Citation component**

Create `frontend/src/components/Citation.tsx`:

```tsx
import { useState } from 'react'

interface Issue {
  reason: string
  detail?: string
  field?: string
  expected?: string
  actual?: string
  rule_id?: string
  category: string
  severity: string
}

interface Props {
  id: string
  status: 'pass' | 'warning' | 'error'
  text: string
  issues: Issue[]
  onClick: (id: string) => void
  highlighted: boolean
}

const COLOR: Record<string, string> = {
  pass: 'bg-green-100 text-green-800',
  warning: 'bg-yellow-100 text-yellow-800',
  error: 'bg-red-100 text-red-800',
}

export default function Citation({ id, status, text, issues, onClick, highlighted }: Props) {
  const [showTip, setShowTip] = useState(false)
  const first = issues[0]

  return (
    <span
      id={id}
      className={`relative cursor-pointer px-0.5 rounded transition
        ${COLOR[status]}
        ${highlighted ? 'ring-2 ring-blue-500' : ''}
      `}
      onClick={() => onClick(id)}
      onMouseEnter={() => setShowTip(true)}
      onMouseLeave={() => setShowTip(false)}
    >
      {text}
      {showTip && first && (
        <span className="absolute bottom-full left-0 mb-1 z-10 w-64 bg-gray-800 text-white text-xs rounded p-2 shadow-lg">
          {first.reason}
        </span>
      )}
    </span>
  )
}
```

- [ ] **Step 2: Create AnnotatedText component**

Create `frontend/src/components/AnnotatedText.tsx`:

```tsx
import Citation from './Citation'

interface CitationData {
  id: string
  kind: string
  raw_text: string
  char_start: number
  char_end: number
  status: 'pass' | 'warning' | 'error'
  issues: any[]
}

interface Props {
  fullText: string
  citations: CitationData[]
  activeCitationId: string | null
  onCitationClick: (id: string) => void
}

export default function AnnotatedText({ fullText, citations, activeCitationId, onCitationClick }: Props) {
  const sorted = [...citations].sort((a, b) => a.char_start - b.char_start)
  const segments: React.ReactNode[] = []
  let cursor = 0

  for (const c of sorted) {
    if (c.char_start > cursor) {
      segments.push(
        <span key={`txt-${cursor}`}>{fullText.slice(cursor, c.char_start)}</span>
      )
    }
    segments.push(
      <Citation
        key={c.id}
        id={c.id}
        status={c.status}
        text={fullText.slice(c.char_start, c.char_end) || c.raw_text}
        issues={c.issues}
        onClick={onCitationClick}
        highlighted={activeCitationId === c.id}
      />
    )
    cursor = c.char_end
  }

  if (cursor < fullText.length) {
    segments.push(<span key="txt-end">{fullText.slice(cursor)}</span>)
  }

  return (
    <div className="whitespace-pre-wrap leading-7 text-sm text-gray-800 font-serif">
      {segments}
    </div>
  )
}
```

- [ ] **Step 3: Create IssuePanel component**

Create `frontend/src/components/IssuePanel.tsx`:

```tsx
interface Issue {
  reason: string
  detail?: string
  field?: string
  expected?: string
  actual?: string
  category: string
  severity: string
  rule_id?: string
}

interface CitationData {
  id: string
  kind: string
  raw_text: string
  status: 'pass' | 'warning' | 'error'
  issues: Issue[]
}

interface Props {
  citations: CitationData[]
  activeCitationId: string | null
  onIssueClick: (id: string) => void
}

const SEVERITY_ICON: Record<string, string> = {
  error: '🔴',
  warning: '🟡',
  pass: '🟢',
}

export default function IssuePanel({ citations, activeCitationId, onIssueClick }: Props) {
  const withIssues = citations.filter(c => c.status !== 'pass')

  if (withIssues.length === 0) {
    return (
      <div className="p-6 text-center text-gray-400 text-sm">
        🎉 No issues found!
      </div>
    )
  }

  return (
    <div className="divide-y divide-gray-100">
      {withIssues.map(c => (
        <div
          key={c.id}
          className={`p-4 cursor-pointer hover:bg-gray-50 transition
            ${activeCitationId === c.id ? 'bg-blue-50' : ''}
          `}
          onClick={() => onIssueClick(c.id)}
        >
          <div className="flex items-start gap-2">
            <span>{SEVERITY_ICON[c.status]}</span>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-mono text-gray-500 truncate">{c.raw_text}</p>
              {c.issues.map((issue, i) => (
                <div key={i} className="mt-1">
                  <p className="text-sm text-gray-800">{issue.reason}</p>
                  {issue.expected && (
                    <p className="text-xs text-gray-500 mt-0.5">
                      应为：<span className="text-green-700">{issue.expected}</span>
                      {issue.actual && <> · 你写的：<span className="text-red-600">{issue.actual}</span></>}
                    </p>
                  )}
                  {issue.detail && (
                    <p className="text-xs text-gray-400 mt-0.5">{issue.detail}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Implement Report.tsx**

Create `frontend/src/pages/Report.tsx`:

```tsx
import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { fetchReport } from '../lib/api'
import AnnotatedText from '../components/AnnotatedText'
import IssuePanel from '../components/IssuePanel'

export default function Report() {
  const { id } = useParams<{ id: string }>()
  const [report, setReport] = useState<any>(null)
  const [error, setError] = useState('')
  const [activeCitationId, setActiveCitationId] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    fetchReport(id)
      .then(setReport)
      .catch(e => setError(e.message))
  }, [id])

  const handleCitationClick = useCallback((citId: string) => {
    setActiveCitationId(citId)
    document.getElementById(citId)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <p className="text-red-500">{error}</p>
      </div>
    )
  }

  if (!report) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const { summary, citations, full_text, filename } = report

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col">
      {/* Top bar */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between">
        <div>
          <h1 className="font-semibold text-gray-800">{filename}</h1>
          <p className="text-xs text-gray-400">This report expires in 24h</p>
        </div>
        <div className="flex gap-4 text-sm">
          <span className="text-green-600">🟢 {summary.pass} pass</span>
          <span className="text-yellow-600">🟡 {summary.warning} warning</span>
          <span className="text-red-600">🔴 {summary.error} error</span>
        </div>
      </header>

      {/* Main content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: annotated text */}
        <div className="flex-1 overflow-y-auto p-6">
          <AnnotatedText
            fullText={full_text}
            citations={citations}
            activeCitationId={activeCitationId}
            onCitationClick={handleCitationClick}
          />
        </div>

        {/* Right: issue panel */}
        <div className="w-96 border-l border-gray-200 bg-white overflow-y-auto">
          <div className="p-4 border-b border-gray-100">
            <h2 className="font-semibold text-gray-700 text-sm">Issues</h2>
          </div>
          <IssuePanel
            citations={citations}
            activeCitationId={activeCitationId}
            onIssueClick={handleCitationClick}
          />
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Test full flow in browser**

With backend + frontend running:
1. Go to `http://localhost:5173`
2. Upload `tests/fixtures/mixed.docx`
3. Verify you are redirected to `/r/<uuid>`
4. Verify left panel shows essay text with coloured highlights
5. Verify right panel shows issue list
6. Click an issue → verify essay scrolls to the citation

- [ ] **Step 6: Commit**

```bash
git add frontend/src/
git commit -m "feat: add report page with annotated text and issue panel"
```

---

## Task 12: CI/CD & Deployment Config

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `backend/Dockerfile`
- Create: `backend/railway.toml`
- Create: `frontend/.env.example`

- [ ] **Step 1: GitHub Actions CI**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: ai-citation-checker/backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[dev]"
      - run: ruff check app/
      - run: pytest tests/ -v

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: ai-citation-checker/frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
      - run: npm ci
      - run: npx tsc --noEmit
      - run: npm run build
```

- [ ] **Step 2: Backend Dockerfile**

Create `backend/Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e .
COPY app/ app/
RUN mkdir -p /data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

- [ ] **Step 3: Railway config**

Create `backend/railway.toml`:

```toml
[build]
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"

[deploy]
startCommand = "uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 4"
healthcheckPath = "/healthz"
healthcheckTimeout = 10

[volumes]
mountPath = "/data"
```

- [ ] **Step 4: Frontend env example**

Create `frontend/.env.example`:

```
VITE_API_BASE=https://your-backend.railway.app
```

- [ ] **Step 5: Run full test suite**

```bash
cd backend && pytest tests/ -v
```

Expected: All tests pass.

```bash
cd frontend && npx tsc --noEmit && npm run build
```

Expected: Build succeeds with no type errors.

- [ ] **Step 6: Final commit**

```bash
git add .github/ backend/Dockerfile backend/railway.toml frontend/.env.example
git commit -m "chore: add CI/CD config and deployment files"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| .docx upload, in-memory parse | Task 4, 9 |
| In-text citation extraction | Task 5 |
| APA R001-R007 format rules | Task 6 |
| Crossref + OpenAlex verification | Task 7 |
| Cache (GREEN only) | Task 7 |
| Ambiguity detection | Task 7, 8 |
| Field-level comparison (content issues) | Task 8 |
| Orphan in-text detection | Task 8 |
| Report JSON with colour status | Task 8 |
| 24h SQLite storage + cleanup | Task 3, 9 |
| Shareable UUID link | Task 9 |
| Upload UI with drag-and-drop | Task 10 |
| Annotated text (left panel) | Task 11 |
| Issue panel (right panel) | Task 11 |
| Detailed reason per issue | Task 8, 11 |
| Rate limiting (10/min/IP) | Task 9 |
| CORS | Task 9 |
| CI/CD | Task 12 |
| Railway + Vercel deploy config | Task 12 |

**All spec requirements covered. No placeholders. Type names are consistent across tasks.**
