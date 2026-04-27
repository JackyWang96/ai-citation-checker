import pytest
import respx
import httpx
from httpx import AsyncClient, ASGITransport
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

@pytest.fixture
async def app_client():
    from app.main import create_app
    from app.storage.db import init_db
    import os, tempfile
    import app.config as cfg
    with tempfile.TemporaryDirectory() as tmp:
        db_path = f"{tmp}/test.db"
        os.environ["DB_PATH"] = db_path
        cfg.DB_PATH = db_path
        app = await create_app()
        await init_db(db_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
