"""Startup behaviour when the APA rules index cannot be trusted.

The design called for fail-fast at boot. This deliberately does not do that: a
stale index is a deployment mistake in one optional feature, and refusing to
boot would take document checking, uploads and reports down with it. These
tests pin the chosen behaviour — disable RAG, log at ERROR, keep serving —
because nothing else exercised it.
"""
from __future__ import annotations

import logging

import pytest

import app.config as cfg
from app.main import _check_rules_index
from app.services import rules_retriever as rr


@pytest.fixture(autouse=True)
def _reset_handle(monkeypatch):
    monkeypatch.setattr(rr, "_db", None)
    yield
    monkeypatch.setattr(rr, "_db", None)


def test_healthy_index_keeps_rag_enabled(monkeypatch):
    monkeypatch.setattr(cfg, "RAG_ENABLED", True)
    _check_rules_index()
    assert cfg.RAG_ENABLED is True


def test_missing_index_disables_rag_without_raising(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(cfg, "RAG_ENABLED", True)
    monkeypatch.setattr(cfg, "RULES_DB_PATH", str(tmp_path / "absent.db"))
    with caplog.at_level(logging.ERROR):
        _check_rules_index()          # must not raise — the app still boots
    assert cfg.RAG_ENABLED is False
    assert any("index unusable" in r.message for r in caplog.records), \
        "a silent downgrade would hide a deployment mistake"


def test_stale_index_disables_rag(monkeypatch, tmp_path, caplog):
    """Fingerprint mismatch is a deployment mistake, not a transient fault."""
    monkeypatch.setattr(cfg, "RAG_ENABLED", True)
    monkeypatch.setattr(cfg, "EMBEDDING_MODEL", "text-embedding-3-large")
    with caplog.at_level(logging.ERROR):
        _check_rules_index()
    assert cfg.RAG_ENABLED is False


def test_check_is_a_no_op_without_an_embedding_key(monkeypatch):
    """No OPENAI_API_KEY means retrieval was never going to run; the index is
    irrelevant and must not be opened."""
    monkeypatch.setattr(cfg, "RAG_ENABLED", False)
    monkeypatch.setattr(cfg, "RULES_DB_PATH", "/definitely/not/here.db")
    _check_rules_index()
    assert cfg.RAG_ENABLED is False


def test_app_still_serves_with_an_unusable_index(monkeypatch, tmp_path):
    """The whole point of not failing fast: boot the real app through its
    lifespan with a broken index and confirm the core endpoints still answer.
    The earlier tests call the helper directly, which cannot show this."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(cfg, "RAG_ENABLED", True)
    monkeypatch.setattr(cfg, "RULES_DB_PATH", str(tmp_path / "absent.db"))
    monkeypatch.setattr(cfg, "DB_PATH", str(tmp_path / "reports.db"))

    from app.main import _make_app

    with TestClient(_make_app()) as client:      # __enter__ runs the lifespan
        assert client.get("/healthz").status_code == 200
    assert cfg.RAG_ENABLED is False, "a broken index must disable RAG, not the app"
