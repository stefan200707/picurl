import asyncio
import contextlib
import os

import asyncpg
import pytest

from app.ai.embeddings import embed
from app.ai.memory import (
    close_pool,
    get_pool,
    lookup_semantic,
    lookup_structured_fact,
    store_semantic,
    store_structured_fact,
)

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql://postgres:password@localhost:5432/picurl_ai"
)


def is_db_available():
    async def _check():
        try:
            conn = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=1.0)
            await conn.close()
            return True
        except Exception:
            return False

    try:
        return asyncio.run(_check())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not is_db_available(), reason="Database not available")


@pytest.fixture(scope="session", autouse=True)
def setup_db_schema():
    if not is_db_available():
        return

    async def _setup():
        pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
        migration_path = os.path.join(
            os.path.dirname(__file__), "../../app/ai/migrations/01_memory_tables.sql"
        )
        with open(migration_path, encoding="utf-8") as f:
            sql = f.read()
        with contextlib.suppress(Exception):
            await pool.execute(sql)
        await pool.close()

    asyncio.run(_setup())


@pytest.fixture(autouse=True)
async def db_transaction():
    """Отчистка перед каждым тестом"""
    pool = await get_pool()
    await pool.execute("TRUNCATE TABLE ai_structured_facts, ai_semantic_cache RESTART IDENTITY")
    yield
    await close_pool()


@pytest.mark.asyncio
async def test_structured_fact_lifecycle():
    await store_structured_fact("complex", "c1", "is_center", {"present": True}, "ai", 0.9)

    fact = await lookup_structured_fact("complex", "c1", "is_center")
    assert fact is not None
    assert fact.fact_value == {"present": True}
    assert fact.confidence == 0.9
    assert fact.observed_count == 1

    await store_structured_fact("complex", "c1", "is_center", {"present": True}, "ai", 0.95)
    fact2 = await lookup_structured_fact("complex", "c1", "is_center")
    assert fact2.observed_count == 2
    assert fact2.confidence == 0.95


@pytest.mark.asyncio
async def test_semantic_cache_lifecycle():
    q1 = "какой-то очень сложный запрос про зелень"
    emb1 = embed(q1)

    await store_semantic("signature1", emb1, q1, {"answer": "зелень рядом"})

    # 1. Точное повторение
    res1 = await lookup_semantic("signature1", emb1, threshold=0.15)
    assert res1 is not None
    assert res1.raw_question == q1
    assert res1.answer == {"answer": "зелень рядом"}
    assert res1.hit_count == 2

    # 2. Близкий по смыслу
    q2 = "хочу чтобы рядом были деревья и зелень"
    emb2 = embed(q2)
    res2 = await lookup_semantic("signature2", emb2, threshold=0.6)
    assert res2 is not None
    assert res2.answer == {"answer": "зелень рядом"}

    # 3. Совсем другой
    q3 = "далеко от метро, вторичка"
    emb3 = embed(q3)
    res3 = await lookup_semantic("signature3", emb3, threshold=0.15)
    assert res3 is None
