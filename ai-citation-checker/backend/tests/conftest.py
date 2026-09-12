"""Test-wide defaults.

The fix loop calls the rules retriever, which embeds its query through
OpenAI. A developer machine with OPENAI_API_KEY set would therefore make real
network calls from the unit suite — slow, non-deterministic, and different
from CI, where no key exists. Disable retrieval by default; the tests that
exercise it opt back in explicitly.
"""
import pytest

import app.config as cfg


@pytest.fixture(autouse=True)
def _rag_disabled_by_default(monkeypatch):
    monkeypatch.setattr(cfg, "RAG_ENABLED", False)
