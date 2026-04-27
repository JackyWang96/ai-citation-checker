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
