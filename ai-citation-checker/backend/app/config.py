import os
from pathlib import Path
from dotenv import load_dotenv

# Load backend/.env if present (local dev convenience). Path is resolved
# relative to this file, not the cwd, because `make dev` runs uvicorn from the
# repo root. Real env vars (e.g. Railway) always win — load_dotenv never
# overrides an already-set variable.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DB_PATH = os.getenv("DB_PATH", "/data/reports.db")
CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "jackywangmel96@gmail.com")
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:5173")

# Stage 2 — optional LLM fix suggestions (opt-in, on demand).
# The feature is disabled unless an API key is present, so the core app runs
# with no LLM dependency and never incurs API cost on a normal check.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
LLM_ENABLED = bool(ANTHROPIC_API_KEY)
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5")
# Cap how many citations are in the fix loop at once. Each one holds the
# slot for its embedding call and up to LLM_MAX_FIX_ATTEMPTS Claude calls,
# so this bounds the whole pipeline, not just the Claude requests.
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "4"))
# Attempts per citation in the validate/retry loop. 2 means at most one
# retry: the first pass fixes most references, and a second failure
# usually means the rules can't be satisfied from the text available.
LLM_MAX_FIX_ATTEMPTS = int(os.getenv("LLM_MAX_FIX_ATTEMPTS", "2"))
# Explicit per-call limits for the two paid APIs. Left to the SDK defaults —
# 600s read timeout, two retries — a single Claude call could legitimately run
# for half an hour, and the analysis lease below had nothing real to be sized
# against.
CLAUDE_TIMEOUT_SECONDS = float(os.getenv("CLAUDE_TIMEOUT_SECONDS", "60"))
EMBEDDING_TIMEOUT_SECONDS = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "30"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

# The longest each SDK will wait between retries when the server sends a
# Retry-After header. Both honour it in preference to their own 8s backoff
# cap, and they differ: anthropic accepts up to 60s, openai up to 120s.
# tests/test_analysis_claims.py checks these against the installed SDKs, so a
# release that raises either cap fails the build instead of silently making
# the lease too short.
ANTHROPIC_MAX_RETRY_AFTER_SECONDS = 60
OPENAI_MAX_RETRY_AFTER_SECONDS = 120

ANALYSIS_LEASE_MARGIN_SECONDS = int(os.getenv("ANALYSIS_LEASE_MARGIN_SECONDS", "60"))

for _name in ("CLAUDE_TIMEOUT_SECONDS", "EMBEDDING_TIMEOUT_SECONDS",
              "ANALYSIS_LEASE_MARGIN_SECONDS", "LLM_CONCURRENCY",
              "LLM_MAX_FIX_ATTEMPTS"):
    if globals()[_name] <= 0:
        # Zero or negative would hand out leases that are already expired, so
        # workers would start paying with no protection at all.
        raise ValueError(f"{_name} must be positive, got {globals()[_name]}")
if LLM_MAX_RETRIES < 0:
    raise ValueError(f"LLM_MAX_RETRIES must be >= 0, got {LLM_MAX_RETRIES}")


def _call_upper_bound(timeout: float, retry_after_cap: float) -> float:
    """Worst case for one SDK call: every attempt times out, and every gap
    between attempts waits the longest Retry-After the SDK will honour."""
    return timeout * (LLM_MAX_RETRIES + 1) + retry_after_cap * LLM_MAX_RETRIES


def analysis_lease_seconds(citation_count: int) -> int:
    """How long to lease `citation_count` citations for.

    Derived from the per-call limits rather than guessed. One citation does an
    embedding and up to LLM_MAX_FIX_ATTEMPTS Claude calls, all inside a single
    concurrency slot; the slots run in ceil(n / LLM_CONCURRENCY) rounds.

    The result is long — about 16 minutes for one round at the defaults. That
    is the honest bound, and its only cost is slower recovery after a crash.
    A shorter lease can expire while a worker is still paying, which lets a
    second request take over and pay again.

    This bounds the work, not the process: a worker paused by the OS beyond
    the bound can still outlive its lease. The tombstones in citation_claims
    stop such a worker overwriting a successor's result; they cannot refund
    the second payment.
    """
    per_citation = (
        _call_upper_bound(EMBEDDING_TIMEOUT_SECONDS, OPENAI_MAX_RETRY_AFTER_SECONDS)
        + LLM_MAX_FIX_ATTEMPTS
        * _call_upper_bound(CLAUDE_TIMEOUT_SECONDS, ANTHROPIC_MAX_RETRY_AFTER_SECONDS)
    )
    rounds = -(-citation_count // LLM_CONCURRENCY)   # ceil division
    return int(ANALYSIS_LEASE_MARGIN_SECONDS + rounds * per_citation)

# langchain-core pulls in langsmith, whose tracing client uploads prompts
# and completions to an external service when enabled. It is off unless
# opted in; set it explicitly so references and their content cannot start
# leaving the process because an upstream default changed.
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")

# RAG — APA rules index. The index is a read-only artifact committed to the
# repo and copied into the image; these values MUST match what
# `build_rules_index` recorded in its index_meta, or the query vectors and the
# indexed vectors live in different spaces. rules_retriever enforces that.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
RULES_DB_PATH = os.getenv("RULES_DB_PATH", str(_BACKEND_DIR / "data" / "apa_rules.db"))
RULES_CORPUS_DIR = os.getenv("RULES_CORPUS_DIR", str(_BACKEND_DIR / "data" / "apa_rules"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))
EMBEDDING_NORMALIZED = os.getenv("EMBEDDING_NORMALIZED", "true").lower() == "true"
# Retrieval is a separate opt-in from generation: OpenAI does embeddings,
# Anthropic does generation, and either key can be absent independently.
RAG_ENABLED = bool(OPENAI_API_KEY)
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
