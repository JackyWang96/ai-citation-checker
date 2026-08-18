# APA 规则 RAG + LangGraph 自校验循环 — 设计文档

**日期**:2026-08-09
**状态**:已通过 Codex 独立交叉评审(4 critical 全部关闭,verdict `done`);**待用户批准后实现**
**所属**:Stage 2 增强(在已上线的 opt-in 修正建议之上)

---

## 1. 目标

让 AI 修正建议**有据可依、且经过验证**:

1. **RAG**:检测到引用问题时,从 APA 7th 格式指引语料中语义检索相关规则原文,作为 grounding 喂给 Claude。
2. **自校验循环**:用项目已有的确定性规则引擎(`apa7.py`)重新校验 LLM 给出的修正,不通过则带着失败原因重试(上限 2 轮);**最终仍未通过的建议明确标记为未验证,不得以"已验证"形态展示**。

### 解决的现有缺口

| 现状 | 加了之后 |
|---|---|
| 建议只说"加了逗号",依据来自模型记忆 | 附上 APA 指引原文 + 来源链接,用户可核查 |
| 对**已检出**的问题,修正依据仅来自模型记忆 | 修正以检索到的官方指引为准,覆盖 R001–R020 之外的引用类型(前提:该引用已被某条规则标记) |
| **LLM 的建议本身从不检查** | 每条建议都跑一遍我们的规则校验,并**如实标注 verified / unverified** |

> ⚠️ **能力边界(交叉评审 W1 修正)**:RAG 增强的是「**已被检出问题的引用**」的修正质量。当前 `_fixable()` 只挑有 `format_violation` / `field_mismatch` 的引用,因此**完全没有触发任何规则的引用不会进入本流程**——RAG 不会凭空发现新的违规。要覆盖"我们没写规则的违规类型",需要另做检出侧的改动,不在本设计范围内。

## 2. 非目标(本次不做)

- 选项 B(相关文献推荐 / 大规模语料向量库)——留待后续,底座会复用
- 内容语义匹配("被引论文是否支撑论点")
- 替换现有的确定性规则引擎——RAG 是**增强**,不是取代
- Postgres / pgvector 迁移(留给 Stage 3)

## 3. 架构总览

```
                   ┌──────────── 构建期(Docker build)────────────┐
                   │  APA 指引文本 → 切块 → embedding → apa_rules.db │
                   │  (一次性,产物烤进镜像)                          │
                   └──────────────────┬──────────────────────────┘
                                      │ 只读
  运行期(用户点「AI 修正建议」)          ▼
  ┌───────────────────────────────────────────────────────────┐
  │  LangGraph                                                │
  │                                                           │
  │   ① retrieve_rules ──→ ② generate_fix ──→ ③ validate_fix  │
  │        (向量检索)         (Claude Haiku)      (apa7 规则)   │
  │                              ▲                     │      │
  │                              └──── 未通过且 <2 轮 ───┘      │
  │                                          通过 或 达上限 →   │
  └─────────────────────────────────────────────────────┼─────┘
                                                        ▼
                              修正建议 + verified 标志(见 §7.5)
                              通过 → ✨ 已验证 / 未通过 → ⚠️ 未验证草稿
```

**关键性质**:③ 是**本地正则**,零成本零延迟——这让循环几乎免费。

## 4. 组件一:APA 规则语料

### 4.1 来源与版权

⚠️ **不得使用 APA 官方出版手册全文**(有版权)。使用公开可访问的指引:

| 来源 | 用途 |
|---|---|
| `apastyle.apa.org/style-grammar-guidelines/references/*` | 主来源,APA 官方公开指引页 |
| Purdue OWL APA Guide | 补充,覆盖官方页未展开的场景 |

**合规做法**:只存**摘要重述 + 我们自己整理的规则说明**,附原文 URL;不整段复制受版权保护的段落。检索结果展示给用户时,给的是"依据 + 链接",不是原文转载。

### 4.2 语料规模与形态

预计 **250–400 chunk**,静态,极少变动。每 chunk:

```yaml
chunk_id:   apa7-journal-volume-issue
category:   journal-article        # journal-article | book | chapter | web | software | thesis | dataset | conference
title:      期刊文章的卷号、期号与页码
text:       |
  期刊名之后写卷号(斜体),紧接期号(不斜体、置于圆括号内),然后是页码范围。
  期刊名与卷号之间必须用逗号分隔。若期刊使用文章编号(article number)而非
  连续页码,则在卷号后直接给出文章编号。
  例:Journal of Abnormal Psychology, 128(6), 510-516.
  例(文章编号):System, 95, 102366.
source_url: https://apastyle.apa.org/style-grammar-guidelines/references/examples/journal-article-references
related_rules: [R010, R020]        # 与我们硬编码规则的对应(可空)
```

**切块策略**:按**语义单元**切,不按固定 token 数——一个格式规则一块。这类语料天然是结构化的(每种引用类型一节),按小节切最自然,避免把"卷期页码规则"切成两半。目标 80–250 token/块。

### 4.3 语料如何进仓库

`backend/data/apa_rules/*.yaml`,人工整理 + 校对后提交。**语料是源码的一部分**,不是运行时抓取——这样可审计、可 diff、构建可复现。

## 5. 组件二:向量存储(sqlite-vec,烤进镜像)

### 5.1 为什么这么选(已验证)

- ✅ **sqlite-vec v0.1.9 实测可用**,Python 扩展加载未被禁用,KNN 检索正常
- ✅ 保持现有 SQLite 技术栈,**零新增基础设施**
- ✅ Railway **没有配置持久化 volume**(已确认 `railway.toml` 无 volume 配置),索引若在运行时生成,每次重部署就丢失 → **烤进镜像是唯一干净解**
- ✅ 语料静态,构建时生成完全合理;无冷启动 ingest 成本

### 5.2 表结构

独立数据库 `apa_rules.db`(与 `reports.db` 分开——前者只读、随镜像发布;后者可写、24h TTL):

```sql
-- 元数据表(普通表)
CREATE TABLE rule_chunks (
    rowid         INTEGER PRIMARY KEY,
    chunk_id      TEXT UNIQUE NOT NULL,
    category      TEXT NOT NULL,
    title         TEXT NOT NULL,
    text          TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    related_rules TEXT              -- JSON array
);

-- 向量表(sqlite-vec 虚拟表,rowid 与上表对应)
CREATE VIRTUAL TABLE rule_vectors USING vec0(
    embedding float[{DIM}]          -- 维度由 embedding 模型决定
);

-- 索引指纹:运行时必须校验,否则用错模型的向量会静默给出错误排序
CREATE TABLE index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- 必填键:embedding_provider / embedding_model / embedding_dim /
--         normalized / corpus_sha256 / built_at / sqlite_vec_version
```

### 5.3 连接与检索

**⚠️ 每个连接都必须显式加载 sqlite-vec 扩展**,否则查询 `vec0` 虚拟表会报 `no such module: vec0`——「Python 支持加载扩展」不等于「该连接已注册 vec0 模块」。

```python
import sqlite3, sqlite_vec

def open_rules_db(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, check_same_thread=False)
    db.enable_load_extension(True)
    sqlite_vec.load(db)                 # 缺这一步 → no such module: vec0
    db.enable_load_extension(False)
    return db
```

**启动期自检**(fail fast,不要等到用户点按钮才发现):

```python
# 任一不一致 → 向量不可比 / 检索结果失效,必须硬失败。
# normalized 尤其危险:维度相同、不报错,但排序静默失真。
_FINGERPRINT_KEYS = (
    "embedding_provider",   # 不同厂商向量空间不通
    "embedding_model",      # 同厂商不同模型也不通
    "embedding_dim",        # 维度不符会直接报错
    "normalized",           # 归一化设置不符 → 静默错误排序
    "corpus_sha256",        # 语料改了但索引没重建 → 返回过时规则
)

def verify_rules_index(db, cfg, corpus_sha256: str) -> None:
    """校验索引指纹与运行时配置完全一致;任一不符立即 fail fast。"""
    meta = dict(db.execute("SELECT key, value FROM index_meta").fetchall())
    runtime = {
        "embedding_provider": cfg.EMBEDDING_PROVIDER,
        "embedding_model":    cfg.EMBEDDING_MODEL,
        "embedding_dim":      str(cfg.EMBEDDING_DIM),
        "normalized":         str(cfg.EMBEDDING_NORMALIZED).lower(),
        "corpus_sha256":      corpus_sha256,
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
            f"sqlite_vec={meta.get('sqlite_vec_version')}] "
            + " | ".join(mismatches)
        )
    db.execute("SELECT vec_version()")   # 确认扩展确实加载成功
```

`built_at` 与 `sqlite_vec_version` **不参与硬校验**(不影响向量可比性),仅作为诊断信息写进错误消息,便于定位是哪次构建的索引。

检索本身仍是一句 SQL:

```python
rows = db.execute(
    """SELECT c.chunk_id, c.title, c.text, c.source_url, v.distance
       FROM rule_vectors v JOIN rule_chunks c ON c.rowid = v.rowid
       WHERE v.embedding MATCH ? AND k = ?
       ORDER BY v.distance""",
    (pack(query_vec), top_k),
).fetchall()
```

这正是**不引入 LangChain retriever 抽象**的理由——省不下几行,却要背 sqlalchemy + numpy。

> ⚠️ **交叉评审 C3 修正**:早期草案的检索片段没有加载扩展,照抄会在运行时直接失败。`sqlite-vec` 版本需在 `pyproject.toml` 中固定(实测 v0.1.9)。

### 5.4 Dockerfile 改动

**决定:索引产物提交进仓库,构建期不生成、不需要任何密钥。**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e .

COPY app/ app/
COPY data/ data/                      # 含已提交的 apa_rules.db

# 只校验产物存在,不生成(构建期零 API 调用、零密钥、可复现)
RUN test -f /app/data/apa_rules.db
```

索引**不在 Docker 构建期生成**。生成是**本地/CI 的维护动作**,语料变更时手动执行:

```bash
# 维护命令(需要 embedding key,只在开发者机器/CI 上跑)
python -m app.scripts.build_rules_index --out data/apa_rules.db
git add data/apa_rules.db data/apa_rules/   # 语料与索引一起提交
```

> ⚠️ **交叉评审 C2 修正**:早期草案在 Dockerfile 里用 BuildKit secret 跑生成,与"提交产物"的决定自相矛盾,且在 Railway 上会因缺少 `/run/secrets/embed_key` 直接构建失败。已移除。

## 6. 组件三:检索层

`services/rules_retriever.py`(约 60 行)。

### ⚠️ 查询里绝不能塞完整引用原文(PR 1 实测结论)

**查询构造是本设计最关键、也最容易做错的一环。** PR 1 阶段用真实索引做了召回评估,结论明确:

| 查询构造 | top-1 命中 | top-3 命中 | 距离区间 |
|---|---|---|---|
| **完整引用原文 + 问题描述** | 3/6 | 4/6 | 0.90–1.16 |
| **问题描述 + 结构化类型线索** | **4/6** | **6/6** | **0.67–0.95** |

原因:引用原文里的**专有名词**(人名、书名、期刊名)贡献了大量语义噪声,把"哪里出了问题"这个真正的信号压了下去。最典型的证据是 Van Vu 那条——`apa7-author-name-format` 块里**字面就写着 "Van Vu, D."**,但把完整引用塞进查询后**反而检索不到它**,只用问题描述却能命中。

**正确做法**:查询 = 检出的问题描述 + **从引用结构推断的类型线索**,不含任何专有名词。

```python
def build_query(citation: dict) -> str:
    """检出的问题 + 引用类型线索;不含人名/书名/期刊名。"""
    hints = type_hints(citation["raw_text"])   # 复用 _is_chapter / _journal_name 等既有判定
    reasons = [i["reason"] for i in citation["issues"] if i["type"] in _FIXABLE_ISSUE_TYPES]
    return f"{'. '.join(reasons)}. Reference type: {hints}."
```

`type_hints()` 由引用的**结构**推断,复用 `apa7.py` 里已有的 `_is_chapter` / `_journal_name`,以及软件标签、`Retrieved from`、`(n.d.)`、文章编号等形态判定,输出如:

```
"chapter in an edited book, with editors after In; has a page range"
"journal article with a periodical name; article number or eLocator instead of a page range"
```

**验收标准**:PR 2 必须用真实 bug 样本(Leuckert / Schmitt / Ellis / Wang / Meichenbaum / Van Vu)做召回评估,**top-3 命中率 ≥ 6/6**;低于此值先调查询构造或语料,不要靠调大 k 掩盖。

由于检索的是 top-3 并全部喂给 Claude,**top-3 命中率才是有效指标**,top-1 不是。

## 7. 组件四:LangGraph 自校验循环

### 7.1 依赖

只装 `langgraph`(实测直接依赖 6 个:langchain-core、langgraph-checkpoint、langgraph-prebuilt、langgraph-sdk、pydantic〈已有〉、xxhash)。
**不装 `langchain-community`**(会带来 sqlalchemy / numpy / aiohttp / langsmith)。

### 7.2 State 定义

```python
class FixState(TypedDict):
    citation:      dict          # 原始引用 + 检测到的 issues
    rules:         list[dict]    # 检索到的规则 chunk(节点①产出)
    suggestion:    str           # 当前修正建议(节点②产出)
    explanation:   str
    validation:    list[str]     # 校验失败项(节点③产出),空 = 通过
    attempts:      int           # 已尝试轮数
    verified:      bool          # 最终是否通过校验 —— 必须如实向上传递
```

### 7.3 节点与边

```python
graph = StateGraph(FixState)
graph.add_node("retrieve", retrieve_rules)     # 向量检索,不调 LLM
graph.add_node("generate", generate_fix)       # Claude Haiku
graph.add_node("validate", validate_fix)       # 本地正则,零成本

graph.set_entry_point("retrieve")
graph.add_edge("retrieve", "generate")
graph.add_edge("generate", "validate")
graph.add_conditional_edges("validate", should_retry, {
    "retry": "generate",   # 回到生成,带上失败反馈(不重新检索——规则没变)
    "done":  END,
})

MAX_ATTEMPTS = 2

def should_retry(state: FixState) -> str:
    if not state["validation"]:            # 校验通过
        return "done"
    if state["attempts"] >= MAX_ATTEMPTS:  # 达到上限 → 退出,但 verified=False
        return "done"
    return "retry"
```

**重试回到 `generate` 而非 `retrieve`**:规则检索结果不会因为重试而改变,省一次向量检索。

### 7.4 检索失败必须 fail open(交叉评审 W3 修正)

现有 `fix_suggester` 的既有契约是**逐条降级**:某条引用调用失败就没建议,不影响其它条目。新增的检索环节**不得破坏这个契约**——embedding provider 故障、索引缺失/损坏、sqlite-vec 加载失败、检索异常,都**不能让整个功能挂掉**。

```python
def retrieve_rules(state: FixState) -> FixState:
    try:
        return {**state, "rules": retriever.search(build_query(state["citation"]))}
    except Exception:
        logger.warning("rules retrieval failed; degrading to non-RAG suggestion",
                       exc_info=True)
        return {**state, "rules": []}      # 空规则 → 退化为现有的无 RAG 行为
```

`rules == []` 时 prompt 不含 guidance 段,等价于**当前已上线的 Stage 2 行为**——即最坏情况是"回到今天的水平",而不是功能不可用。

> 例外:`verify_rules_index()` 的**启动期**校验仍然 fail fast(§5.3)。区别在于:启动期错配是**部署问题**,应当拦住;运行期单次检索失败是**瞬时故障**,应当降级。

### 7.5 未通过校验时的交付语义(交叉评审 C1 修正)

早期草案让循环在达到上限时"交付当前最好结果",与 §1 声称的"必须通过校验才展示"**自相矛盾**——那等于把一个**已知不合规**的建议当成已验证的展示给用户。修正为:

```python
verified = not state["validation"]
```

| 结果 | `suggestion` | `verified` | 前端展示 |
|---|---|---|---|
| 校验通过 | 修正串 | `True` | ✨ 建议修正(附依据规则) |
| 达上限仍失败 | 修正串 | **`False`** | ⚠️ **"AI 草稿,未通过自动校验"** + 列出仍未解决的校验项 |
| LLM 全程失败 | `None` | — | 无建议(与现状一致) |

**契约**:`verified=False` 的建议**不得**进入"已验证"展示路径。Schema 增加 `suggestion_verified: bool`;前端按此分流,视觉上明显区分。

对应测试:`test_unverified_suggestion_never_marked_verified` —— 构造一个永远无法通过校验的场景,断言输出 `suggestion_verified is False` 且不走已验证渲染分支。

### 7.4 ⚠️ 校验器适配(已实测的坑)

**实测发现**:把 LLM 给的纯文本建议丢回 `validate_reference_paragraph`,会**误报 R003(斜体)**——纯字符串没有斜体信息,校验器以为"没斜体"。

`services/suggestion_validator.py`(约 30 行)必须**过滤掉依赖排版的规则**:

```python
# 纯文本无法判断的规则:斜体、悬挂缩进
_FORMATTING_ONLY_RULES = frozenset({"R003", "R006"})

def validate_suggestion(text: str) -> list[str]:
    para = ReferenceParagraph(raw_text=text, runs=[], has_hanging_indent=True)
    return [
        f"[{i.rule_id}] {i.reason}"
        for i in validate_reference_paragraph(para)
        if i.rule_id not in _FORMATTING_ONLY_RULES
    ]
```

可校验的:R001/R002/R005/R007–R011/R014–R017/R020。

## 8. Prompt 模板

### 8.1 System(稳定 → 走 prompt caching)

在现有 system prompt 基础上追加:

```
你会收到从 APA 7th 官方格式指引中检索出的相关规则。修正时以这些规则为准;
若检索到的规则与你的判断冲突,以规则为准。在解释中简要说明依据了哪条规则。
```

### 8.2 User(第 1 轮)

```
Reference:
{原始引用}

Detected problems:
- {reason} (expected: {expected}) (found: {actual})

Relevant APA 7th guidance:
[1] {chunk.title}
{chunk.text}
Source: {chunk.source_url}

[2] ...
```

### 8.3 User(重试轮)—— 关键设计

⚠️ **每次重试都是一次独立的 API 调用,模型没有对话记忆**——LangGraph 的 state 只在我们进程内,不会自动进 prompt。因此重试请求必须**重新携带全部原始材料**,不能只发失败项:

```
Reference:
{原始引用}                      ← 必须重发

Detected problems:
- {原始检出的问题}                ← 必须重发

Relevant APA 7th guidance:
[1] {检索到的规则 chunk}          ← 必须重发(检索结果复用,不重新检索)

--- 上一次尝试未通过自动校验 ---

Your previous attempt:
{上一轮 suggestion}

Validation failures:
- [R010] Missing comma between journal name and volume (APA 7th R010)

Fix these specific failures while keeping everything else correct.
Do not introduce new bibliographic facts.
```

把**具体的校验失败项**回灌,而不是笼统说"不对,重来"——这是循环能收敛的原因。

> ⚠️ **交叉评审 W2 修正**:早期草案的重试模板只含"上一轮结果 + 失败项",照此实现会让模型在重试时失去原始引用和规则依据。对应测试须断言**第二次请求的完整内容**,而不只是"含有失败信息"。

### 8.4 输出(structured output,沿用现有 schema 并扩展)

```json
{
  "corrected_reference": "...",
  "explanation": "...",
  "rule_basis": ["apa7-journal-volume-issue"]
}
```

⚠️ **`rule_basis` 是模型生成的,不可信**。展示前必须过滤:

```python
retrieved_ids = {c["chunk_id"] for c in state["rules"]}
rule_basis = [cid for cid in (data.get("rule_basis") or []) if cid in retrieved_ids]
# 标题与来源链接一律从数据库行取,绝不用模型输出的字段
```

> ⚠️ **交叉评审 W4 修正**:未校验的 `rule_basis` 可能是幻觉出的 chunk_id,导致前端显示错误依据或无效链接——即"用假引用来佐证修正",对一个反引用造假的工具是致命的讽刺。

## 9. Embedding provider(已定:OpenAI)

**决定:OpenAI `text-embedding-3-small`。**

| 项 | 值 |
|---|---|
| Provider | `openai` |
| 模型 | `text-embedding-3-small` |
| 价格 | **$0.02 / 百万 token**(2026-08 核实) |
| 维度 | 默认 **1536**;**不使用** `dimensions` 截断参数(截断后需重新归一化,徒增出错面) |
| 归一化 | OpenAI 返回单位长度向量 → `normalized=true`;**构建脚本按 API 实际返回值记录,不写死假设** |
| 新增 env | `OPENAI_API_KEY`(仅 embedding 用)、`EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` / `EMBEDDING_NORMALIZED` |

**为什么可以接受"项目里出现两家 provider"**:职责完全隔离——Anthropic 只做生成,OpenAI 只做 embedding,互不依赖;两个 key 各自缺失时的降级路径也独立(见 §7.4)。

**实际花费**:
- 建索引一次:400 块 × ~150 tok ≈ 60k tok ≈ **$0.0012**
- 每份文档:20 条 × ~30 tok ≈ 600 tok ≈ **$0.000012**
- 1000 份/月 ≈ **$0.012**(一分二)

> 备选(不采用):Voyage `voyage-4-lite` 同价且有 200M 免费额度;本地 sentence-transformers 名义免费,但 ~500MB 模型 + 常驻内存在按资源计费的 Railway 上**反而更贵**,且冷启动变慢。

### ⚠️ 换 provider 的真实代价(交叉评审 C4 修正)

早期草案称"换 provider 只改 `embeddings.py` 一个文件"——**这是错的**。不同 provider / 不同模型版本的向量**不共享向量空间**,维度也可能不同。拿 A 模型的查询向量去检索 B 模型建的索引,轻则排序无意义、重则维度不匹配报错,而且**可能不报错地静默给出垃圾结果**——这是最危险的形态。

正确表述:

| 换什么 | 代价 |
|---|---|
| 调用代码 | 改 `embeddings.py` 一个文件(约 40 行) |
| **索引** | **必须整体重建**(重跑 `build_rules_index` 并重新提交 `apa_rules.db`) |
| 安全网 | `index_meta` 记录 provider/model/dim/normalized/corpus_sha256;**启动期校验不一致就 fail fast**(见 §5.3),杜绝静默错配 |

语料只有 250–400 块,重建成本极低(几秒 + < $0.01),所以这不是阻碍——**只是必须记录并强制校验,不能假装可以热插拔**。

## 10. 成本与延迟

按 Haiku 4.5($1/百万输入、$5/百万输出),单条引用:

| | 输入 | 输出 | 成本 |
|---|---|---|---|
| 第 1 轮(含检索规则) | ~730 tok | ~100 tok | $0.0012 |
| 重试轮 | ~880 tok | ~100 tok | $0.0014 |

假设 25% 需一轮重试:

```
单条平均 ≈ $0.0016
一份 20 条可修引用的文档 ≈ $0.03(三分钱)
相比无循环版本 ≈ +$0.006/文档
```

延迟:20 条、并发 4 → 约 **12–13 秒**(校验本身 < 1ms,不计)。

## 11. 测试计划

全部**不联网**(embedding 与 Claude 均用假 client):

| 测试 | 验证什么 |
|---|---|
| `test_rules_retriever_returns_relevant_chunk` | 给定 R010 问题,检索到期刊卷期页码规则 |
| `test_retriever_handles_type_without_hardcoded_rule` | 百科条目(无对应硬编码规则)也能检索到指引 |
| `test_suggestion_validator_ignores_formatting_only_rules` | **R003/R006 不因纯文本被误报**(已实测的坑) |
| `test_graph_exits_immediately_when_valid` | 首轮通过 → 只调 1 次 LLM |
| `test_graph_retries_with_failure_feedback` | 首轮失败 → 第 2 次 prompt 含具体失败项 |
| `test_graph_respects_max_attempts` | 始终失败 → 最多 2 轮,不无限循环 |
| **`test_unverified_suggestion_never_marked_verified`** | **达上限仍失败 → `suggestion_verified is False`,不进已验证展示路径(C1)** |
| **`test_rules_db_requires_sqlite_vec_loaded`** | **用未加载扩展的连接查 `rule_vectors` 应报错;`open_rules_db()` 则正常(C3)** |
| **`test_index_meta_mismatch_fails_fast`** | **指纹任一项(provider/model/dim/normalized/corpus_sha256)不一致 → 启动期 RuntimeError;逐项参数化,含 `normalized` 这类不报维度错却会静默失真的情形(C4)** |
| `test_graph_degrades_when_llm_fails` | LLM 异常 → 该条无建议,不影响其它条目 |

| **`test_retry_request_carries_full_context`** | **第 2 次请求完整包含原始引用 + 检出问题 + 检索到的规则,不只是失败项(W2)** |
| **`test_retrieval_failure_degrades_to_non_rag`** | **embedding/索引/检索异常 → 该条退化为无 RAG 建议,其它条目不受影响(W3)** |
| **`test_rule_basis_filtered_to_retrieved_ids`** | **模型返回不存在的 chunk_id → 被过滤,不出现在展示中(W4)** |

所有行为回归测试**写入 `backend/tests/test_regression.py`**,每个带描述 bug/行为的 docstring;每个 PR 的完成门槛是 `cd backend && pytest` 全绿(遵循项目 CLAUDE.md 约定)。

## 12. PR 拆分(逐个可独立合并)

| PR | 内容 | 规模 |
|---|---|---|
| **1** | 语料 + 索引构建脚本 + `apa_rules.db` 产物 + Dockerfile 拷贝 | 语料 250–400 块;脚本 ~80 行 |
| **2** | `embeddings.py` + `vectors.py` + `rules_retriever.py` + 测试 | ~150 行 |
| **3** | `suggestion_validator.py` + `fix_graph.py`(LangGraph)+ 接入 `fix_suggester` + 测试 | ~150 行 |
| **4** | 前端:建议卡片展示"依据规则 + 来源链接" | ~40 行 |

PR 1–2 合并后即使 PR 3 未完成也不影响现有功能(检索层未被调用)。

## 13. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 语料版权 | 只用公开指引页 + 自行重述,附来源链接,不整段转载 |
| 检索召回不准 | PR 2 阶段用真实 bug 样本(Leuckert/Ellis/Schmitt/Wang)做召回评估,不达标就调整切块粒度 |
| 循环不收敛 | 硬上限 2 轮;达上限的结果**标记 `verified=False` 并以"未通过校验的草稿"展示**,绝不冒充已验证(见 §7.5) |
| 索引与运行时 embedding 配置错配 | `index_meta` 指纹 + 启动期 fail-fast 校验(§5.3);换 provider 必须重建索引(§9) |
| 检索链路故障拖垮整个功能 | 运行期 fail open,退化为现有无 RAG 行为(§7.4);启动期错配才 fail fast |
| 模型幻觉出的规则依据 | `rule_basis` 与实际检索到的 chunk_id 取交集;标题/链接只从 DB 取(§8.4) |
| sqlite-vec 未加载导致运行时失败 | 统一经 `open_rules_db()` 建连接;`pyproject.toml` 固定版本;测试覆盖(§5.3) |
| **纯文本无法校验排版规则** | **已识别**:显式过滤 R003/R006,并在测试中锁定 |
| LangGraph API 变动 | 只用最基础的 StateGraph/节点/条件边,不用高级特性 |
| 镜像体积增加 | 索引约 2–5 MB,可忽略;不装 langchain-community 避免 numpy/sqlalchemy |

---

## 决策记录

| 决策 | 结论 | 时间 |
|---|---|---|
| 方向 | A(APA 规则知识库),B(文献推荐)延后 | 2026-08-09 |
| 向量存储 | sqlite-vec + 索引提交进仓库 | 2026-08-09 |
| Embedding provider | **OpenAI `text-embedding-3-small`** | 2026-08-09 |
| 编排 | LangGraph 自校验循环(Codex 建议纯循环,用户保留 LangGraph) | 2026-08-09 |
| 交叉评审 | Codex 独立评审,4 critical 全部关闭(verdict `done`) | 2026-08-09 |

## 待批准事项

1. **整体设计是否认可** → 认可后从 PR 1 开始
