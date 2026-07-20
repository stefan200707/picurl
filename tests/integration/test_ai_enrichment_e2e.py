"""Test AI enrichment integration in the build_url endpoint."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.endpoints import get_http_client, get_memory_pool
from app.config import get_settings
from app.main import app
from app.ai.schema import AIEnrichmentAnswer


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
def mock_pool():
    class MockConnection:
        async def fetchrow(self, *args, **kwargs):
            return None
            
        async def execute(self, *args, **kwargs):
            pass

    class MockTransaction:
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
    class MockAcquiredConnection:
        async def fetchrow(self, *args, **kwargs):
            return None
            
        async def execute(self, *args, **kwargs):
            pass
            
        def transaction(self):
            return MockTransaction()
            
        async def __aenter__(self):
            return self
            
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockPool:
        def acquire(self):
            return MockAcquiredConnection()

        async def fetchrow(self, *args, **kwargs):
            return None
            
        async def execute(self, *args, **kwargs):
            pass
            
        async def close(self):
            pass
            
    return MockPool()


@pytest.fixture
def ai_enabled_client(mock_validator_client, mock_pool, monkeypatch):
    """Client with AI enrichment enabled and mocked DB pool."""
    monkeypatch.setenv("AI_ENRICHMENT_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
    
    app.dependency_overrides[get_http_client] = lambda: mock_validator_client
    app.dependency_overrides[get_memory_pool] = lambda: mock_pool
    
    # We must patch get_settings to reload because it might be cached
    settings = get_settings()
    settings.AI_ENRICHMENT_ENABLED = True
    settings.ANTHROPIC_API_KEY = "test-key-123"

    with TestClient(app) as c:
        yield c
        
    app.dependency_overrides.clear()
    settings.AI_ENRICHMENT_ENABLED = False


def test_ai_enrichment_enabled(ai_enabled_client, monkeypatch):
    """Test AI enrichment flow when enabled."""
    
    # Mock Anthropic model call
    async def mock_call_model(*args, **kwargs):
        return AIEnrichmentAnswer(
            matched_complex_ids=["1709"],  # Just some random known ID for complex
            center_district_ids=[],
            poi_findings={"1709": {"school": True, "kindergarten": True}},
            explanation="Test explanation",
            confidence=0.9
        )
        
    monkeypatch.setattr("app.ai.enrichment.call_model", mock_call_model)
    monkeypatch.setattr("app.ai.enrichment.embed", lambda x: [0.0]*384)
    
    text = "рядом школа и детский сад в центре"
    response = ai_enabled_client.post("/build-url", json={"text": text})
    assert response.status_code == 200
    data = response.json()
    
    assert data["ai_used"] is True
    assert data["ai_cache_hit"] is False
    assert data["ai_explanation"] == "Test explanation"
    
    # It should have mapped "286" back to complexes
    assert len(data["criteria"]["complexes"]) == 1
    
    # Warnings should not contain "школа" or "детский сад" as unrecognized text
    warnings_str = " ".join(data["warnings"])
    assert "школа" not in warnings_str
    assert "детский сад" not in warnings_str


def test_ai_enrichment_disabled_graceful_degradation(mock_validator_client, monkeypatch):
    """Test AI enrichment graceful degradation when disabled."""
    monkeypatch.setenv("AI_ENRICHMENT_ENABLED", "false")
    settings = get_settings()
    settings.AI_ENRICHMENT_ENABLED = False
    
    app.dependency_overrides[get_http_client] = lambda: mock_validator_client
    # No memory pool provided to simulate it not being initialized
    app.dependency_overrides[get_memory_pool] = lambda: None
    
    with TestClient(app) as client:
        text = "рядом школа и детский сад в центре"
        response = client.post("/build-url", json={"text": text})
        
    assert response.status_code == 200
    data = response.json()
    
    assert data["ai_used"] is False
    assert data["ai_cache_hit"] is False
    
    # It should add a warning instead of failing
    assert any("ИИ-обогащение выключено" in w for w in data["warnings"])
    
    app.dependency_overrides.clear()
