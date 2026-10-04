"""Test-wide defaults.

The unit suite must never reach a paid API. That has happened twice: once
because the fix loop's retriever embedded queries through OpenAI, and once
because a test exercised suggest_fixes without injecting a fake client and
made a real, billed Anthropic request. Both only on machines with keys
configured, so CI — which has none — behaved differently and stayed green.

Remembering to inject a fake is not enough, so the real constructors are
replaced outright. A test that needs a client passes one in; a test that
forgets now fails immediately instead of quietly spending money.
"""
import anthropic
import openai
import pytest

import app.config as cfg


class RealApiClientInUnitTest(RuntimeError):
    pass


def _refuse(name):
    def build(*args, **kwargs):
        raise RealApiClientInUnitTest(
            f"a real {name} client was constructed in a unit test; inject a "
            "fake client instead"
        )
    return build


@pytest.fixture(autouse=True)
def _no_paid_api_calls(monkeypatch):
    monkeypatch.setattr(cfg, "RAG_ENABLED", False)
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _refuse("Anthropic"))
    monkeypatch.setattr(anthropic, "Anthropic", _refuse("Anthropic"))
    monkeypatch.setattr(openai, "AsyncOpenAI", _refuse("OpenAI"))
    monkeypatch.setattr(openai, "OpenAI", _refuse("OpenAI"))
