# AI 引用检测器 — 设计文档

**日期：** 2026-04-26
**作者：** Jacky Wang
**状态：** 草稿（待审阅）

---

## 1. 背景与问题

大学生越来越多地使用 AI（ChatGPT、Claude、Gemini）撰写 essay。AI 生成的 essay 经常出现以下三类引用问题：

1. **虚假文献（Fabricated references）** —— 引用的论文根本不存在（LLM 幻觉）。
2. **元数据错误（Incorrect metadata）** —— 论文存在，但作者 / 年份 / 期刊 / 页码写错。
3. **格式错误（Format errors）** —— 违反引用格式（APA / MLA 等）规则（斜体、悬挂缩进、作者首字母、DOI 格式等）。

学生通常要等到导师批改时才发现这些问题 —— 为时已晚。目前没有一个**快速、免费、端到端**的工具能在提交前帮学生审计 essay 的引用。

## 2. 目标

做一个 Web 应用，学生上传 `.docx` essay 后，**5 秒内**收到一份带颜色标注的报告，每条引用被分类为 **通过 / 警告 / 错误**，并附带可操作的解释。

## 3. 不做的事（MVP 范围外）

- 多种引用格式（MLA / Harvard / Chicago / IEEE）—— **只做 APA 7th**。
- 内容匹配（"被引文献是否真的支持这个论点？"）—— 推迟到 Stage 2（需要 LLM）。
- **In-text 引用的内容验证** —— 不对比正文里的年份/页码与 reference list 是否一致；只检查格式 + 是否在 reference list 中存在。
- PDF 输入 —— **只支持 `.docx`**。
- 用户账号、历史记录、付费功能。
- 自动修复 / 一键改正。
- 浏览器插件 / Google Docs 集成。

## 4. MVP 范围（已锁定）

| 维度 | MVP | Next Stage |
|---|---|---|
| 产品形态 | Web 应用 | 浏览器插件 |
| 引用格式 | APA 7th | + MLA / Harvard / Chicago |
| 检测层级 | Reference：存在性 + 字段匹配；In-text：仅格式 + 孤儿检查 | + In-text 内容匹配（年份/页码）；+ LLM 语义匹配 |
| 报告形态 | 标注版（颜色 + 点击查看详情） | + 修复建议 + PDF 导出 |
| 文档格式 | `.docx` | + `.pdf` |
| 用户 / 存储 | 无登录 + 24h 临时分享链接 | 可选账号 + 历史记录 |

## 5. 用户流程

1. 学生打开主页 → 拖拽 `essay.docx`。
2. 前端 `POST /api/check`（multipart 上传）。
3. 后端解析、提取引用、调用 Crossref 验证、跑 APA 校验、拼报告 JSON、写入 SQLite（`expires_at = now + 24h`）。
4. 后端返回 `{ report_id, url: "/r/<uuid>" }`。
5. 前端跳转到 `/r/<uuid>`。页面布局：
   - **左栏**：完整 essay 文本，每个 in-text 引用用彩色 `<span>` 包裹（绿 / 黄 / 红）。
   - **右栏**：可滚动的问题列表；点击问题 → 跳转并高亮左栏对应位置。
   - **顶栏**：总分 + 计数（`12 通过 / 2 警告 / 1 错误`）。
6. 链接 24 小时内可分享，过期后定时任务自动删除记录。

## 6. 架构

### 6.1 系统图

```
┌─────────────────────────────────────────────┐
│  浏览器  (React + Vite + TypeScript)         │
│  上传页 → 进度条 → 标注报告                  │
└──────────────────┬──────────────────────────┘
                   │ HTTPS（Vercel 部署）
                   ▼
┌─────────────────────────────────────────────┐
│  FastAPI  (Python 3.11，Railway 部署)        │
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
│ /data/       │               │（免费）      │
│ reports.db   │               │              │
└──────────────┘               └──────────────┘
```

### 6.2 模块职责

| 模块 | 职责 | 关键库 |
|---|---|---|
| `docx_parser.py` | 从内存读取 `.docx`（BytesIO，不写盘），提取段落并保留斜体信息（用于期刊名校验），识别 "References" / "Bibliography" / "Works Cited" 标题 | `python-docx` |
| `citation_extractor.py` | (a) 正则提取 in-text 引用：`(Smith, 2020)`、`(Smith & Jones, 2020, p. 15)`、`(Smith et al., 2020)`。(b) 用悬挂缩进/空行启发式拆分 reference list 的每条条目 | stdlib `re` |
| `verifier.py` | 对每条 reference：提取 title + 第一作者 + 年份，调 Crossref `/works?query.bibliographic=...`，用模糊匹配挑出权威记录，返回 DOI + 元数据。用 `asyncio.gather` 并发（最多 10 路）。Crossref 没找到则 fallback 到 OpenAlex | `httpx`、`rapidfuzz` |
| `apa_validator.py` | 对每条 reference 跑 APA 7th 规则集（作者格式、年份括号、斜体、DOI 格式、悬挂缩进）。纯本地，无 I/O | 规则在 `rules/apa7.py` |
| `report_builder.py` | 交叉对比 in-text 与 reference list（孤儿检查），汇总每条引用的 issue，计算总分，生成 `Report` JSON | Pydantic |
| `storage/db.py` | 异步 SQLite（`aiosqlite`），单表 `reports`，不用 ORM | `aiosqlite` |
| `storage/cleanup.py` | APScheduler 每小时跑：`DELETE FROM reports WHERE expires_at < ?` | `APScheduler` |

### 6.3 数据模型

```python
class CitationIssue(BaseModel):
    type: Literal["not_found", "field_mismatch", "format_violation", "orphan", "ambiguous"]
    severity: Literal["red", "yellow"]
    category: Literal["content", "format", "orphan", "ambiguous"]   # UI 分组用
    reason: str             # 一句话说明原因，显示在标注 tooltip 里
    detail: Optional[str]   # 补充说明（如列出同年其他文章、引用规则编号）
    field: Optional[str]    # 出问题的字段："author" / "year" / "journal" 等
    expected: Optional[str] # 权威值
    actual: Optional[str]   # 学生写的值
    rule_id: Optional[str]  # APA 规则编号，format 类型专用（如 "R003"）

class Citation(BaseModel):
    id: str                 # "c1", "c2", ...
    kind: Literal["intext", "reference"]
    raw_text: str           # 学生原文
    char_start: int         # 在 full_text 中的位置（高亮用）
    char_end: int
    status: Literal["pass", "warning", "error"]
    issues: list[CitationIssue]
    verified_reference_id: Optional[str]  # FK to verified_references.id（仅 GREEN 有；YELLOW/RED 为 None）

class Report(BaseModel):
    id: str                 # 128 位 UUID，URL 安全
    filename: str
    created_at: datetime
    expires_at: datetime
    full_text: str
    citations: list[Citation]
    summary: dict           # {"total": 15, "pass": 10, "warning": 3, "error": 2}
```

SQLite 表结构：

```sql
CREATE TABLE reports (
    id          TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    filename    TEXT NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    expires_at  TIMESTAMP NOT NULL
);
CREATE INDEX idx_reports_expires_at ON reports(expires_at);

-- 已验证文献的永久缓存（仅 GREEN）。
-- 只有三字段完全匹配（score=100 + author + year）才写入。
-- 后续报告引用同一文献直接命中，无需再调 API。
CREATE TABLE verified_references (
    id                      TEXT PRIMARY KEY,
    doi                     TEXT UNIQUE,
    title_normalized        TEXT NOT NULL,
    first_author_normalized TEXT NOT NULL,
    year                    INTEGER NOT NULL,
    canonical_json          TEXT NOT NULL,
    source                  TEXT NOT NULL,
    cached_at               TIMESTAMP NOT NULL
);
CREATE INDEX idx_vr_doi    ON verified_references(doi) WHERE doi IS NOT NULL;
CREATE INDEX idx_vr_lookup ON verified_references(title_normalized, first_author_normalized, year);
```

### 6.4 API 接口

| Method | Path | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/check` | `multipart/form-data`，字段 `file` | `{ "report_id": "...", "url": "/r/..." }` |
| GET | `/api/report/{id}` | — | `Report` JSON，过期返回 `410 Gone` |
| GET | `/healthz` | — | `{ "ok": true }`（Railway 健康检查） |

### 6.5 前端

- **技术栈**：React 18 + Vite + TypeScript + Tailwind CSS。
- **页面**：`Upload.tsx`、`Report.tsx`。
- **关键组件**：
  - `AnnotatedText.tsx` —— 渲染 `full_text`，在 `char_start..char_end` 位置插入 `<Citation>` span。
  - `IssuePanel.tsx` —— 虚拟滚动列表（应对大报告），点击 → 滚动到对应引用。
  - `Citation.tsx` —— 彩色 span，hover 显示首条 issue，点击 → 聚焦右栏。
- **路由**：`react-router-dom`，`/r/:id` 解析到 `Report` 页。

## 7. 检测逻辑详解

MVP 使用**简化的三色判定**：

- 🔴 **RED** —— 仅当 reference 在 Crossref + OpenAlex 都查不到时触发。
- 🟡 **YELLOW** —— reference 存在但有任何字段差异（content）或 APA 格式违规（format）；in-text 引用格式有误或在 reference list 找不到对应条目。
- 🟢 **GREEN** —— reference 存在且所有字段匹配；in-text 引用格式正确且能在 reference list 找到对应条目。

### 7.1 Reference list — 存在性检查（含缓存）

对每条解析出的 reference：

1. 提取 `doi`（如学生写了）、`title`、`first_author`、`year`。
2. **缓存查询**（命中则跳过 Crossref）：
   - 有 DOI → `SELECT * FROM verified_references WHERE doi = ?`
   - 无 DOI → `SELECT * FROM verified_references WHERE title_normalized = ? AND first_author_normalized = ? AND year = ?`
   - 命中 → 直接用 `canonical_json`，进入 §7.2。
3. **缓存未命中** → 调 Crossref：
   - `https://api.crossref.org/works?query.bibliographic=<title+author+year>&rows=5&mailto=<contact>`
   - `mailto` 参数让我们进入 polite pool（~100 req/s）。
4. 对每个候选：计算 `rapidfuzz.token_set_ratio(student_title, candidate_title)`。
5. `score = 100` 且 author 匹配 且 `|year| <= 1` → 精确匹配（GREEN 候选）。
   - 写入 `verified_references`（source = "crossref"），进入 §7.2。
   `score 85-99` → 可能匹配（YELLOW 候选），**不写入缓存**，进入 §7.2。
6. Crossref 无匹配 → fallback 到 OpenAlex（`https://api.openalex.org/works?search=...`）。
   - 精确匹配（score=100）→ 写入 `verified_references`（source = "openalex"），进入 §7.2。
   - 模糊匹配（85-99）→ 不写缓存，进入 §7.2。
7. 两个都失败 → 进入 AI 编造信号检测（§7.5）。

### 7.2 Reference list — 字段级对比

匹配到权威记录后，逐字段对比。**任何**不一致都产生一条 🟡 YELLOW issue，每条 issue 标注 `category: "content"` 或 `category: "format"` 以便 UI 分组。

| 字段 | 比对规则 | Issue 分类 |
|---|---|---|
| 第一作者姓 | 精确（不区分大小写） | content |
| 所有作者姓（含顺序） | 精确（不区分大小写） | content |
| 年份 | 精确 | content |
| 标题 | `token_set_ratio >= 95` | content |
| 期刊名 | `token_set_ratio >= 90` | content |
| 卷号 | 学生写了则精确比对 | content |
| 期号 | 学生写了则精确比对 | content |
| 页码 | 学生写了则精确比对 | content |
| DOI | 学生写了则精确比对 | content |

一条 reference 判为 🟢 GREEN 需同时满足：(a) 找到，(b) 零内容不一致，(c) 段落零 APA 格式问题（§7.4）。

### 7.3 In-text 引用 — 格式与存在性

In-text 引用**仅做格式校验** —— **不**与 reference list 对比内容（例如不会检查 `(Smith, 2020, p. 15)` 与 `Smith (2020). ... pp. 12-30` 的年份/页码是否一致，这放到 Stage 2）。

对每条提取出的 in-text 引用：

| 检查项 | 规则 | 失败结果 |
|---|---|---|
| **APA 括号格式** | 匹配 `(Author[, Author...]\s*,\s*\d{4}([a-z])?(\s*,\s*p+\.\s*\d+)?)` 或叙述形式 `Author (Year)` | 🟡 YELLOW（`category: "format"`）—— 例：缺括号、缺逗号、大小写错误 |
| **作者大小写** | 姓首字母大写 | 🟡 YELLOW（format） |
| **三人及以上作者** | 第一次引用即用 `et al.`（APA 7th） | 🟡 YELLOW（format） |
| **在 reference list 中存在** | 引用的「作者 + 年份」组合至少匹配 reference list 的一条 | 🟡 YELLOW（`category: "orphan"`）—— 「in-text 找不到对应 reference」 |

通过所有检查的 in-text 引用 → 🟢 GREEN。

**MVP 范围外**：不对比 in-text 的年份/页码与匹配 reference 的权威元数据。

### 7.4 APA 7th 格式规则（reference list 段落）

写在 `rules/apa7.py` 中的纯函数。每条规则接受一个解析后的 reference 段落，返回 `Optional[CitationIssue]`（带 `category: "format"`）。任何规则触发 → 该 reference 降级为 🟡 YELLOW（不能再为 GREEN）。

- `R001` 作者格式 `Last, F. M.`（首字母带点）。
- `R002` 年份在括号内，紧跟作者。
- `R003` 期刊名斜体（检查 python-docx 的 `run.italic`）。
- `R004` 卷号斜体；期号在括号内，不斜体。
- `R005` DOI 用 URL 格式 `https://doi.org/...`，不用 `doi:...`。
- `R006` Reference 段落用悬挂缩进（`paragraph_format.first_line_indent < 0`）。
- `R007` 多作者用逗号分隔，最后一个用 `, & `。

规则可以增量添加，每条独立可测。

### 7.5 状态判定汇总

```
Reference 条目状态：
  Crossref + OpenAlex 都查不到              → 🔴 RED
  找到，但有任何内容不一致                   → 🟡 YELLOW（content）
  找到，但有任何 APA 格式违规                → 🟡 YELLOW（format）
  找到，但同作者同年存在多篇文章             → 🟡 YELLOW（ambiguous）
  找到、全部匹配、零格式问题、无歧义         → 🟢 GREEN

In-text 引用状态：
  违反 APA 括号格式规则           → 🟡 YELLOW（format）
  在 reference list 找不到对应条目 → 🟡 YELLOW（orphan）
  其他                            → 🟢 GREEN
```

## 8. 性能预算

以一篇 2000 字、15 条引用的 essay 为基准：

| 阶段 | 目标 | 备注 |
|---|---|---|
| `.docx` 解析 | < 300 ms | python-docx 单线程 |
| 引用提取 | < 100 ms | 纯文本正则 |
| Crossref 验证 | < 4 s | 并发 10 路，由最慢的 API 调用决定 |
| APA 校验 | < 100 ms | 本地 |
| 写库 + 返回 | < 200 ms | SQLite 写 |
| **端到端** | **< 5 s** | |

前端用确定性进度条，通过 SSE 或轮询 `/api/report/{id}` 驱动（必要时加 status 字段）。

## 9. 隐私与安全

- 上传的 `.docx` 在内存中解析，**不写入磁盘**。只持久化结构化的报告 JSON。
- Report ID 使用 128 位 UUID（不可猜测，无需鉴权）。
- 所有报告 24h 后由 APScheduler 自动删除。
- Railway / Vercel 强制 HTTPS。
- CORS 白名单：仅生产前端域名。
- 默认不收集 PII，不接入 analytics。
- `POST /api/check` 通过 `slowapi` 限流 10 req/min/IP（防止滥用 Crossref 免费配额）。

## 10. 部署

| 层 | 平台 | 成本 |
|---|---|---|
| 前端 | Vercel 免费版（静态托管 + CDN） | $0 |
| 后端 | Railway Hobby（常驻容器，单实例） | ~$5/月 |
| 数据库 | Railway Volume 上的 SQLite（1 GB） | 含在内 |
| 域名 | Cloudflare（`.com`） | $10/年 |

CI：GitHub Actions 在每个 PR 跑 `pytest` + `ruff` + 前端 `vitest` + `tsc`。Railway 监听 `main` 分支自动部署后端，Vercel 自动部署前端。

## 11. 测试策略

- **单元测试**（pytest）：每个 service 模块，特别是 `citation_extractor`（正则边界）和 `apa7` 规则。覆盖率 80%+。
- **集成测试**：`tests/fixtures/` 下放一组小型 `.docx` essay，覆盖以下场景：

| Fixture 文件 | 测试场景 |
|---|---|
| `clean.docx` | 所有引用正确 → 全绿 |
| `fabricated.docx` | 完全虚假文献（Crossref + OpenAlex 都查不到）→ 全红 |
| `format_errors.docx` | 文献存在但 APA 格式违规（作者首字母、斜体、DOI 格式）→ 黄 |
| `metadata_mismatch.docx` | 文献存在但作者/年份/期刊写错 → 黄 |
| `ambiguous_author.docx` | 同作者同年多篇文章，学生标题匹配到其中一篇 → 黄（ambiguous 警告） |
| `missing_subtitle.docx` | 学生省略副标题（score 85-99）→ 黄（标题不完整） |
| `orphan_intext.docx` | In-text 有引用但 reference list 里没有对应条目 → 黄（orphan） |
| `mixed.docx` | 以上多种情况混合 |

- **Crossref Mock**：用 `respx` 录制 HTTP 响应，测试可离线、确定性。对 `ambiguous_author` 场景 mock 返回多个同作者同年的候选结果。
- **前端**：vitest 测组件；Playwright 跑「上传 → 报告」冒烟测试。

## 12. 风险与应对

| 风险 | 影响 | 应对 |
|---|---|---|
| Crossref 限流 | 长 essay 检测失败 | Polite pool（`mailto`）、指数退避、按 IP 限流 |
| Crossref 找不到非英文 / 小众文献 | 误报「不存在」 | OpenAlex fallback |
| 畸形 `.docx`（图片、表格、公式） | 解析崩 | `try/except` 包住，返回友好错误 |
| Reference 段落标题变种（"Bibliography"、无标题） | 提取不到 reference list | 多种 heading 正则 + 末尾 N 段启发式 |
| 单进程瓶颈 | 多人同时用排队 | Uvicorn `--workers 4`（Railway 512MB tier 支持）|

## 13. 演进路径（Stages 2 → 4）

MVP 架构支持以下每个阶段**无需结构性改写**：

- **Stage 2 — LLM 内容匹配**：新增 `services/content_matcher.py` 调 Claude API；新增 `CitationIssue.type = "content_mismatch"`。无需改 schema。
- **Stage 3 — 账号与历史**：SQLite 迁 Postgres（SQLAlchemy 或原生 SQL 都行），加 `users` 表 + Google OAuth，给 `reports` 加 `user_id`。
- **Stage 4 — 浏览器插件**：插件复用现有 `/api/check` 接口；只是新加一个 Manifest V3 前端。

## 14. 待解决问题

设计阶段无 —— 主要决策已通过 brainstorming 锁定。实施计划阶段会暴露战术问题（具体正则、Crossref 响应边界 case）。

---

## 附录 A — 默认速率限制

- Crossref polite pool：100 req/s 持续，约 50 baseline。
- OpenAlex：免费 100k req/day，10 req/s burst。
- 我们的公开限流：10 essays/min/IP（最差约 150 Crossref calls/min，远低于配额）。

## 附录 B — 文件目录结构

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
