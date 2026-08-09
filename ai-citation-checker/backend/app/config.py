import os

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
