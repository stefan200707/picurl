import json
import os
from datetime import datetime
from typing import Any

import asyncpg
from pydantic import BaseModel

# Для настройки подключения можно использовать переменные окружения.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/picurl_ai")

class StructuredFact(BaseModel):
    id: int
    subject_type: str
    subject_id: str
    fact_type: str
    fact_value: dict[str, Any]
    source: str
    confidence: float
    observed_count: int
    first_seen_at: datetime
    last_confirmed_at: datetime

class CachedAnswer(BaseModel):
    id: int
    query_signature: str
    raw_question: str
    answer: dict[str, Any]
    hit_count: int
    created_at: datetime
    last_used_at: datetime

_pool: asyncpg.Pool | None = None

async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool

async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None

async def lookup_structured_fact(
    subject_type: str, subject_id: str, fact_type: str
) -> StructuredFact | None:
    pool = await get_pool()
    query = """
        SELECT id, subject_type, subject_id, fact_type, fact_value, source, confidence,
               observed_count, first_seen_at, last_confirmed_at
        FROM ai_structured_facts
        WHERE subject_type = $1 AND subject_id = $2 AND fact_type = $3
    """
    row = await pool.fetchrow(query, subject_type, subject_id, fact_type)
    if row:
        return StructuredFact(
            id=row['id'],
            subject_type=row['subject_type'],
            subject_id=row['subject_id'],
            fact_type=row['fact_type'],
            fact_value=json.loads(row['fact_value']),
            source=row['source'],
            confidence=row['confidence'],
            observed_count=row['observed_count'],
            first_seen_at=row['first_seen_at'],
            last_confirmed_at=row['last_confirmed_at']
        )
    return None

async def store_structured_fact(
    subject_type: str,
    subject_id: str,
    fact_type: str,
    value: dict[str, Any],
    source: str,
    confidence: float
) -> None:
    pool = await get_pool()
    query = """
        INSERT INTO ai_structured_facts (
            subject_type, subject_id, fact_type, fact_value, source, confidence
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (subject_type, subject_id, fact_type) DO UPDATE
        SET
            observed_count = ai_structured_facts.observed_count + 1,
            last_confirmed_at = now(),
            confidence = CASE
                WHEN ai_structured_facts.fact_value = $4::jsonb
                THEN GREATEST(ai_structured_facts.confidence, $6)
                ELSE ai_structured_facts.confidence
            END,
            fact_value = CASE
                WHEN ai_structured_facts.fact_value = $4::jsonb THEN ai_structured_facts.fact_value
                ELSE $4::jsonb
            END
    """
    await pool.execute(
        query, subject_type, subject_id, fact_type, json.dumps(value), source, confidence
    )

async def lookup_semantic(
    query_signature: str, embedding: list[float], threshold: float = 0.15
) -> CachedAnswer | None:
    pool = await get_pool()
    # pgvector cosine distance
    query = """
        SELECT id, query_signature, raw_question, answer, hit_count, created_at,
               last_used_at, (embedding <=> $1::halfvec(384)) AS dist
        FROM ai_semantic_cache
        ORDER BY embedding <=> $1::halfvec(384)
        LIMIT 1
    """
    embedding_str = "[" + ",".join(map(str, embedding)) + "]"

    row = await pool.fetchrow(query, embedding_str)
    if row and row['dist'] <= threshold:
        update_query = """
            UPDATE ai_semantic_cache
            SET hit_count = hit_count + 1, last_used_at = now()
            WHERE id = $1
        """
        await pool.execute(update_query, row['id'])

        return CachedAnswer(
            id=row['id'],
            query_signature=row['query_signature'],
            raw_question=row['raw_question'],
            answer=json.loads(row['answer']),
            hit_count=row['hit_count'] + 1,
            created_at=row['created_at'],
            last_used_at=datetime.now()
        )
    return None

async def store_semantic(
    query_signature: str, embedding: list[float], raw_question: str, answer: dict[str, Any]
) -> None:
    pool = await get_pool()
    query = """
        INSERT INTO ai_semantic_cache (query_signature, embedding, raw_question, answer)
        VALUES ($1, $2::halfvec(384), $3, $4)
    """
    embedding_str = "[" + ",".join(map(str, embedding)) + "]"
    await pool.execute(query, query_signature, embedding_str, raw_question, json.dumps(answer))
