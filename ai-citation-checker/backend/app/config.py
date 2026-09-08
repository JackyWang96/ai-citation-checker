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
# Cap concurrent Claude calls so a large document doesn't fan out unbounded.
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "4"))
# Attempts per citation in the validate/retry loop. 2 means at most one
# retry: the first pass fixes most references, and a second failure
# usually means the rules can't be satisfied from the text available.
LLM_MAX_FIX_ATTEMPTS = int(os.getenv("LLM_MAX_FIX_ATTEMPTS", "2"))

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
