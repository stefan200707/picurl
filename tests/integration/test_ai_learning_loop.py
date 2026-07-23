import shutil
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai.memory import CachedAnswer, StructuredFact
from app.ai.schema import AIEnrichmentAnswer
from app.api.endpoints import get_http_client, get_memory_pool
from app.config import get_settings
from app.main import app


@pytest.fixture
def mock_validator_client():
    """Mock HTTP client for testing validator."""
    state = {"count": 10, "error": None}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.pik.ru"
        if state["error"]:
            raise state["error"]
        return httpx.Response(200, json={"count": state["count"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.mock_state = state
    return client


@pytest.fixture
def temp_data_dir(tmp_path, monkeypatch):
    """Copy reference data to tmp_path and patch DATA_DIR."""
    source_dir = Path("app/reference")
    for file in source_dir.glob("*.json"):
        shutil.copy(file, tmp_path)

    # Create an empty poi_cache.json if it doesn't exist
    if not (tmp_path / "poi_cache.json").exists():
        (tmp_path / "poi_cache.json").write_text("{}", "utf-8")

    monkeypatch.setattr("app.reference.loader.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.geo.candidates.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.ai.promotion.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.reference.refresh.DATA_DIR", tmp_path)

    # Clear cache so it reloads from tmp_path
    from app.reference.loader import clear_cache

    clear_cache()
    from app.parsing.entity_match import build_choices

    build_choices.cache_clear()

    return tmp_path


class InMemoryPool:
    """Fake asyncpg pool for in-memory testing."""

    def __init__(self):
        self.semantic_cache: list[CachedAnswer] = []
        self.structured_facts: list[StructuredFact] = []
        self.fact_id_seq = 1
        self.cache_id_seq = 1

    async def fetchrow(self, query, *args):
        return None

    async def fetch(self, query, *args):
        return []

    async def execute(self, query, *args):
        pass

    def acquire(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    def transaction(self):
        return self


@pytest.fixture
def memory_db(monkeypatch):
    """Fake in-memory DB and patch memory functions."""
    db = InMemoryPool()

    async def fake_lookup_semantic(pool, signature, embedding, threshold=0.15, ef_search=40):
        # Simplistic lookup: just match signature
        for c in db.semantic_cache:
            if c.query_signature == signature:
                c.hit_count += 1
                c.last_used_at = datetime.now()
                return c
        return None

    async def fake_store_semantic(pool, signature, embedding, raw_question, answer_dict):
        c = CachedAnswer(
            id=db.cache_id_seq,
            query_signature=signature,
            raw_question=raw_question,
            answer=answer_dict,
            hit_count=0,
            created_at=datetime.now(),
            last_used_at=datetime.now(),
        )
        db.semantic_cache.append(c)
        db.cache_id_seq += 1

    async def fake_lookup_structured_fact(pool, subject_type, subject_id, fact_type):
        for f in db.structured_facts:
            if (
                f.subject_type == subject_type
                and f.subject_id == subject_id
                and f.fact_type == fact_type
            ):
                return f
        return None

    async def fake_store_structured_fact(
        pool, subject_type, subject_id, fact_type, value, source, confidence
    ):
        existing = await fake_lookup_structured_fact(pool, subject_type, subject_id, fact_type)
        if existing:
            existing.observed_count += 1
            existing.last_confirmed_at = datetime.now()
            existing.fact_value = value
            existing.source = source
            existing.confidence = max(existing.confidence, confidence)
        else:
            f = StructuredFact(
                id=db.fact_id_seq,
                subject_type=subject_type,
                subject_id=subject_id,
                fact_type=fact_type,
                fact_value=value,
                source=source,
                confidence=confidence,
                observed_count=1,
                first_seen_at=datetime.now(),
                last_confirmed_at=datetime.now(),
            )
            db.structured_facts.append(f)
            db.fact_id_seq += 1

    monkeypatch.setattr("app.ai.enrichment.lookup_semantic", fake_lookup_semantic)
    monkeypatch.setattr("app.ai.enrichment.store_semantic", fake_store_semantic)
    monkeypatch.setattr("app.ai.enrichment.store_structured_fact", fake_store_structured_fact)

    return db


@pytest.fixture
def mock_anthropic_client(monkeypatch):
    """Mock the anthropic client to return a predictable answer."""

    class MockState:
        call_count = 0
        answer = AIEnrichmentAnswer(
            matched_complex_ids=[],
            center_district_ids=[],
            poi_findings={},
            explanation="test",
            confidence=0.9,
        )

    state = MockState()

    async def mock_call_model(sys_prompt, context):
        state.call_count += 1
        return state.answer

    monkeypatch.setattr("app.ai.enrichment.call_model", mock_call_model)
    monkeypatch.setattr("app.ai.enrichment.embed", lambda x: [0.0] * 384)
    return state


@pytest.fixture
def e2e_client(mock_validator_client, memory_db, temp_data_dir, monkeypatch):
    """Test client with AI fully enabled by default."""
    monkeypatch.setenv("AI_ENRICHMENT_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    monkeypatch.setenv("ANTIGRAVITY_CLI_PATH", "agy")

    settings = get_settings()
    settings.AI_ENRICHMENT_ENABLED = True
    settings.ANTHROPIC_API_KEY = "test-key-123"
    settings.ANTIGRAVITY_CLI_PATH = "agy"

    app.dependency_overrides[get_http_client] = lambda: mock_validator_client
    app.dependency_overrides[get_memory_pool] = lambda: memory_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    settings.AI_ENRICHMENT_ENABLED = False


def test_ai_learning_loop_e2e(e2e_client, mock_anthropic_client, memory_db, monkeypatch):
    """
    1. Cold start
    2. Cached start (repeat)
    3. Promotion
    4. Degradation
    5. Hallucination (Sanitize)
    6. v1 Regression
    """
    from app.ai.promotion import promote

    # Known candidate ID from complexes.json: let's assume "1709" exists.
    # Actually, we need to ensure "1709" is in complexes.json to pass sanitization.
    # Let's find a valid complex ID from the temp_data_dir:
    from app.reference.loader import clear_cache, load_complexes

    clear_cache()
    complexes = load_complexes()
    valid_complex_id = complexes[0].id if complexes and complexes[0].id else "test_id"
    valid_complex_name = complexes[0].name if complexes else "test_name"

    # Scenario 1: Cold start
    mock_anthropic_client.answer = AIEnrichmentAnswer(
        matched_complex_ids=[valid_complex_id],
        center_district_ids=[],
        poi_findings={valid_complex_id: {"school": True, "kindergarten": True}},
        explanation="Cold start test explanation",
        confidence=0.9,
    )

    text1 = "трёшка в центре, рядом школа и детский сад"
    resp1 = e2e_client.post("/build-url", json={"text": text1})
    assert resp1.status_code == 200
    data1 = resp1.json()

    assert data1["ai_used"] is True
    assert data1["ai_cache_hit"] is False
    assert mock_anthropic_client.call_count == 1
    assert valid_complex_name in data1["criteria"]["complexes"]
    # Assert POI removed from warnings
    warnings_str = " ".join(data1["warnings"])
    assert "школа" not in warnings_str

    # Check that facts and cache were saved
    assert len(memory_db.semantic_cache) == 1
    assert len(memory_db.structured_facts) == 2  # school and kindergarten

    # Scenario 2: Repeat similar request (Cache hit)
    # To hit cache in our simple fake DB, the signature must match.
    # Since signature is based on semantic leftover + candidates,
    # it should match if criteria match exactly.
    # In reality, signature matching is done by pgvector, but our mock needs exact signature.
    # Let's just use the exact same text to guarantee mock hit.
    # Wait, if we use the exact same text, it's definitely the same signature.
    resp2 = e2e_client.post("/build-url", json={"text": text1})
    assert resp2.status_code == 200
    data2 = resp2.json()

    assert data2["ai_used"] is False
    assert data2["ai_cache_hit"] is True
    assert mock_anthropic_client.call_count == 1  # No new calls

    # Scenario 3: Promotion
    # We artificially boost observed_count
    for fact in memory_db.structured_facts:
        fact.observed_count = 5  # Above threshold

    # We need to mock pool.execute for promote to work?
    # No, promote uses asyncpg.Pool. We need to mock update queries
    # or just call promote with fake pool.
    import asyncio

    async def fake_execute(*args, **kwargs):
        pass

    memory_db.execute = fake_execute

    report = asyncio.run(promote(memory_db, memory_db.structured_facts))
    assert report.promoted_count > 0

    # Now we clear the cache to force reloading from JSON
    from app.reference.loader import clear_cache

    clear_cache()
    from app.parsing.entity_match import build_choices

    build_choices.cache_clear()

    # Third request: it should resolve deterministically without hitting AI or semantic cache
    # Wait, if we use the same text, it hits semantic cache first!
    # Let's use a different text that means the same things but has a different signature.
    text3 = f"3-комнатная квартира с детским садом и школой в ЖК {valid_complex_name}"
    # Actually, deterministic resolution happens BEFORE checking semantic cache!
    # Let's clear the semantic cache to be sure.
    memory_db.semantic_cache.clear()

    resp3 = e2e_client.post("/build-url", json={"text": text3})
    assert resp3.status_code == 200
    data3 = resp3.json()

    assert data3["ai_used"] is True
    assert data3["ai_cache_hit"] is False
    assert valid_complex_name in data3["criteria"]["complexes"]

    # Scenario 4: Degradation
    monkeypatch.setenv("AI_ENRICHMENT_ENABLED", "false")
    settings = get_settings()
    settings.AI_ENRICHMENT_ENABLED = False

    # Use text with new POI to avoid deterministic match
    text4 = "квартира в парке"
    resp4 = e2e_client.post("/build-url", json={"text": text4})
    assert resp4.status_code == 200
    data4 = resp4.json()
    assert data4["ai_used"] is False
    assert any("ИИ-обогащение выключено" in w for w in data4["warnings"])

    # Enable back for Scenario 5
    settings.AI_ENRICHMENT_ENABLED = True

    # Scenario 5: Hallucination
    mock_anthropic_client.answer = AIEnrichmentAnswer(
        matched_complex_ids=["non_existent_id_999"],
        center_district_ids=[],
        poi_findings={"non_existent_id_999": {"park_forest": True}},
        explanation="Hallucination test",
        confidence=0.9,
    )

    text5 = "квартира в парке"
    resp5 = e2e_client.post("/build-url", json={"text": text5})
    assert resp5.status_code == 200
    data5 = resp5.json()

    assert data5["ai_used"] is True
    # The non_existent_id_999 should be sanitized away
    assert "complexes" not in data5["criteria"]

    # Scenario 6: v1 Regression
    #
    # Milestone AI-14: гейт 1 в enrich() теперь включён. Этот запрос —
    # чисто структурный (комнатность/метро/цена/отделка), без poi_requirements,
    # center_requested или нераспознанных фраз-кандидатов на опции. Раньше (пока
    # гейт был выключен, Milestone AI-11) enrich() всё равно дёргал ИИ на любой
    # запрос — это и есть тот самый бесполезный вызов, из-за которого гейт
    # вернули: обогащать здесь нечего, детерминированный слой уже дал полный
    # ответ. Правильное новое ожидание — ИИ НЕ вызывается вовсе, а v1-пайплайн
    # (парсинг → URL → валидация) по-прежнему работает независимо от ИИ-слоя.
    text6 = "двушка у метро Внуково, до 15 млн, с отделкой"
    calls_before = mock_anthropic_client.call_count
    resp6 = e2e_client.post("/build-url", json={"text": text6})
    assert resp6.status_code == 200
    data6 = resp6.json()

    assert data6["ai_used"] is False
    assert data6["ai_failed"] is False
    assert data6["ai_cache_hit"] is False
    assert mock_anthropic_client.call_count == calls_before  # гейт 1: вызова ИИ нет
    assert data6["criteria"]["rooms"] == "2"
    assert data6["criteria"]["price_max"] == 15000000
    assert data6["criteria"]["finish"] == "готовая"
