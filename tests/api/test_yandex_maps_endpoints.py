"""Тесты API эндпоинтов для интеграции с Яндекс.Картами и маршрутизации."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.endpoints import get_http_client
from app.main import app


@pytest.fixture
def mock_validator_client():
    """Mock HTTP client for testing validator."""
    state = {"count": 10, "error": None}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": state["count"]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client.mock_state = state
    return client


@pytest.fixture
def client(mock_validator_client):
    app.dependency_overrides[get_http_client] = lambda: mock_validator_client
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_build_url_returns_map_config(client):
    response = client.post(
        "/build-url",
        json={"text": "двушка до 15 млн до 20 минут пешком от МГУ"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "url" in data
    assert "map_config" in data
    map_config = data["map_config"]
    assert map_config is not None
    assert map_config["auto_load"] is True
    assert map_config["point_a"] is not None
    assert map_config["point_b"] is not None
    assert "МГУ" in map_config["point_b"]["name"]
    assert map_config["route"] is not None
    assert map_config["route"]["duration_min"] >= 1
    assert "yandex.ru/maps" in map_config["route"]["yandex_maps_url"]
    assert len(map_config["all_complex_routes"]) > 0


def test_calculate_geo_route_endpoint(client):
    response = client.post(
        "/api/geo/route",
        json={
            "origin": {"lat": 55.7029, "lon": 37.5308, "name": "ЖК Матвеевский парк"},
            "destination": {"lat": 55.7028, "lon": 37.5305, "name": "МГУ"},
            "travel_mode": "pedestrian",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "route" in data
    assert data["route"]["travel_mode"] == "pedestrian"
    assert data["route"]["duration_min"] >= 1
    assert len(data["all_modes"]) == 4


def test_get_map_html_page(client):
    response = client.get("/map")
    assert response.status_code == 200
    assert "api-maps.yandex.ru" in response.text
    assert "Поиск новостроек ПИК и Яндекс.Карты" in response.text


def test_get_root_page(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "api-maps.yandex.ru" in response.text
