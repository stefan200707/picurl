import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app, get_http_client


@pytest.fixture
def mock_validator_client():
    """Mock HTTP client for testing validator."""
    state = {"count": 47}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.pik.ru"
        return httpx.Response(200, json={"count": state["count"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    
    # Allow tests to change the mocked count by modifying state
    client.mock_state = state
    return client


@pytest.fixture
def client(mock_validator_client):
    app.dependency_overrides[get_http_client] = lambda: mock_validator_client
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_build_url_empty_text(client):
    response = client.post("/build-url", json={"text": ""})
    assert response.status_code == 422  # validation error for min_length
    
    response = client.post("/build-url", json={"text": "   "})
    assert response.status_code == 400
    assert response.json() == {"detail": "Текст запроса не может быть пустым."}


def test_build_url_success(client, mock_validator_client):
    mock_validator_client.mock_state["count"] = 47

    response = client.post(
        "/build-url",
        json={"text": "хочу двушку у метро аэропорт внуково, до 15 млн, с отделкой"},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["url"] == "https://www.pik.ru/search/two-room/finish/m-aeroport-vnukovo?priceFrom=0&priceTo=15000000"
    assert data["result_count"] == 47
    assert data["warnings"] == []
    
    criteria = data["criteria"]
    assert criteria["rooms"] == "2"
    assert criteria["price_max"] == 15000000
    assert len(criteria["metro"]) == 1
    assert criteria["metro"][0] == "Аэропорт Внуково"
    assert criteria["finish"] is True


def test_build_url_empty_results(client, mock_validator_client):
    mock_validator_client.mock_state["count"] = 0

    response = client.post(
        "/build-url",
        json={"text": "двушка"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["result_count"] == 0
    assert "под критерии ничего не найдено" in data["warnings"]


def test_build_url_unsupported_warning(client, mock_validator_client):
    mock_validator_client.mock_state["count"] = 10

    response = client.post(
        "/build-url",
        json={"text": "хочу двушку вторичка"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["result_count"] == 10
    
    assert any("вторичка" in w and "не попало в ссылку" in w for w in data["warnings"])

