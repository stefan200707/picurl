import asyncio
import contextlib
import os

import asyncpg
import pytest

from app.ai.embeddings import embed
from app.ai.memory import (
    lookup_semantic,
    lookup_structured_fact,
    store_semantic,
    store_structured_fact,
)

os.environ["DATABASE_URL"] = os.getenv(
    "TEST_DATABASE_URL", "postgresql://postgres:password@localhost:5432/picurl_ai"
)

import socket
import urllib.parse


def is_db_available():
    parsed = urllib.parse.urlparse(os.environ["DATABASE_URL"])
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


if not is_db_available():
    pytest.skip("PostgreSQL is not available, skipping memory tests.", allow_module_level=True)


@pytest.fixture(scope="session", autouse=True)
def setup_db_schema():

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


@pytest.fixture
async def db_transaction():
    """Отчистка перед каждым тестом"""
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"])
    await pool.execute("TRUNCATE TABLE ai_structured_facts, ai_semantic_cache RESTART IDENTITY")
    yield pool
    await pool.close()


@pytest.mark.asyncio
async def test_structured_fact_lifecycle(db_transaction):
    pool = db_transaction
    await store_structured_fact(pool, "complex", "c1", "is_center", {"present": True}, "ai", 0.9)

    fact = await lookup_structured_fact(pool, "complex", "c1", "is_center")
    assert fact is not None
    assert fact.fact_value == {"present": True}
    assert fact.confidence == 0.9
    assert fact.observed_count == 1

    await store_structured_fact(pool, "complex", "c1", "is_center", {"present": True}, "ai", 0.95)
    fact2 = await lookup_structured_fact(pool, "complex", "c1", "is_center")
    assert fact2.observed_count == 2
    assert fact2.confidence == 0.95


@pytest.mark.asyncio
async def test_semantic_cache_lifecycle(db_transaction):
    pool = db_transaction
    q1 = "какой-то очень сложный запрос про зелень"
    emb1 = embed(q1)

    await store_semantic(pool, "signature1", emb1, q1, {"answer": "зелень рядом"})

    # 1. Точное повторение
    res1 = await lookup_semantic(pool, "signature1", emb1, threshold=0.15)
    assert res1 is not None
    assert res1.raw_question == q1
    assert res1.answer == {"answer": "зелень рядом"}
    assert res1.hit_count == 2

    # 2. Близкий по смыслу
    q2 = "хочу чтобы рядом были деревья и зелень"
    emb2 = embed(q2)
    res2 = await lookup_semantic(pool, "signature2", emb2, threshold=0.6)
    assert res2 is not None
    assert res2.answer == {"answer": "зелень рядом"}

    # 3. Совсем другой
    q3 = "далеко от метро, вторичка"
    emb3 = embed(q3)
    res3 = await lookup_semantic(pool, "signature3", emb3, threshold=0.15)
    assert res3 is None
