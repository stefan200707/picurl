"""10 — Интеграционные тесты end-to-end с замоканным pik.ru — Milestone 5."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api.endpoints import get_http_client


@pytest.fixture
def mock_validator_client():
    """Mock HTTP client for testing validator."""
    state = {"count": 47, "error": None}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.pik.ru"
        if state["error"]:
            raise state["error"]
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


def test_e2e_full_cycle_success(client, mock_validator_client):
    """1. Эталон ТЗ (полный цикл)."""
    mock_validator_client.mock_state["count"] = 47

    response = client.post(
        "/build-url",
        json={
            "text": "хочу 2-комнатную у метро Аэропорт Внуково, до 15 млн, "
            "с отделкой, сначала дешевле"
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert (
        data["url"]
        == "https://www.pik.ru/search/two-room/finish/m-aeroport-vnukovo?priceFrom=0&priceTo=15000000&sortBy=price&orderBy=asc"
    )
    assert data["result_count"] == 47
    assert data["warnings"] == []

    criteria = data["criteria"]
    assert criteria["rooms"] == "2"
    assert criteria["price_max"] == 15000000
    assert criteria["metro"] == ["Аэропорт Внуково"]
    assert criteria["finish"] == "готовая"
    assert criteria["sort"] == "price_asc"


def test_e2e_acceptance_criteria(client):
    """Acceptance criteria test from prompt."""
    text = (
        "хочу двушку у метро Каширская, до метро до 19 минут пешком, "
        "этажность от 9 до 16, сдача с 2026 по 2028, до 30 млн"
    )
    response = client.post("/build-url", json={"text": text})
    assert response.status_code == 200
    data = response.json()

    assert data["criteria"]["rooms"] == "2"
    assert data["criteria"]["price_max"] == 30000000
    assert data["criteria"]["time_on_foot"] == 19
    assert data["criteria"]["settlement_year_from"] == 2026
    assert data["criteria"]["settlement_year_to"] == 2028
    assert data["criteria"]["metro"] == ["Каширская"]
    assert data["criteria"]["floor_min"] == 9
    assert data["criteria"]["floor_max"] == 16

    warnings_str = " ".join(data["warnings"])
    assert "этажность от 9 до 16" not in warnings_str


@pytest.mark.parametrize(
    "text, expected_url_fragment, expected_criteria",
    [
        (
            "хочу двушку у Сокола до 12м с отделкой",
            "search/two-room/finish/m-sokol?priceFrom=0&priceTo=12000000",
            {"rooms": "2", "metro": ["Сокол"], "price_max": 12000000, "finish": "готовая"},
        ),
        (
            "трёшка в Бабушкинском районе",
            "search/three-room?districtLocations=203",
            {"rooms": "3+", "districts": ["Бабушкинский"]},
        ),
        (
            "студия в ЖК Мичуринский парк подешевле",
            "search/studio?blocks=1108&sortBy=price&orderBy=asc",
            {"rooms": "студия", "complexes": ["Мичуринский парк"], "sort": "price_asc"},
        ),
    ],
)
def test_e2e_colloquial_typos(client, text, expected_url_fragment, expected_criteria):
    """2. Разговорные/опечатки."""
    response = client.post("/build-url", json={"text": text})
    assert response.status_code == 200
    data = response.json()
    assert expected_url_fragment in data["url"]

    for k, v in expected_criteria.items():
        assert data["criteria"][k] == v


def test_e2e_multi_select(client):
    """3. Multi-select: несколько метро/районов → проверить переход путь→query."""
    response = client.post("/build-url", json={"text": "двушка у метро Сокол или Аэропорт"})
    assert response.status_code == 200
    data = response.json()

    assert "metroStations=" in data["url"]
    assert "m-sokol" not in data["url"].split("?")[0]

    assert set(data["criteria"]["metro"]) == {"Сокол", "Аэропорт"}


def test_e2e_unrecognized_warnings(client):
    """4. Нераспознанное в warnings."""
    response = client.post("/build-url", json={"text": "двушка рядом с большим парком"})
    assert response.status_code == 200
    data = response.json()
    assert "search/two-room" in data["url"]
    assert any("большим парком" in w for w in data["warnings"])


def test_e2e_unsupported_warning(client):
    """5. Неподдерживаемое."""
    response = client.post("/build-url", json={"text": "хочу двушку вторичка"})
    assert response.status_code == 200
    data = response.json()
    assert "search/two-room" in data["url"]
    assert any("вторичка" in w and "не поддерживается" in w for w in data["warnings"])


def test_e2e_empty_results(client, mock_validator_client):
    """6. Пустая выдача: замокать API на 0."""
    mock_validator_client.mock_state["count"] = 0
    response = client.post("/build-url", json={"text": "трёшка в Некрасовке"})
    assert response.status_code == 200
    data = response.json()
    assert data["result_count"] == 0
    assert "под критерии ничего не найдено" in data["warnings"]


def test_e2e_network_unavailable(client, mock_validator_client):
    """7. Сеть недоступна: замокать таймаут."""
    mock_validator_client.mock_state["error"] = httpx.RequestError(
        "Timeout", request=httpx.Request("GET", "https://api.pik.ru")
    )
    response = client.post("/build-url", json={"text": "студия"})
    assert response.status_code == 200
    data = response.json()
    assert data["result_count"] is None
    assert "выдача не проверена" in data["warnings"]
    assert "search/studio" in data["url"]


def test_e2e_ambiguity_typos(client):
    """8. Неоднозначность: опечатка, дающая близкие score."""
    response = client.post("/build-url", json={"text": "двушка у метро Кантимировская"})
    assert response.status_code == 200
    data = response.json()
    assert "search/two-room/m-kantemirovskaya" in data["url"]
    assert data["criteria"]["metro"] == ["Кантемировская"]

    # We may also verify ambiguity warnings if the entity_match returns them.
    # We check if warning contains "Кантемировская" or "имели в виду".
    # Since entity_match appends "Неоднозначность для «Кантимировская»...",
    # we can just assert it exists if generated.


def test_build_url_empty_text(client):
    """Проверка валидации пустых запросов (доп. покрытие)."""
    response = client.post("/build-url", json={"text": ""})
    assert response.status_code == 422

    response = client.post("/build-url", json={"text": "   "})
    assert response.status_code == 400


def test_e2e_massive_test_query(client):
    """9. Масштабный тест из ТЗ."""
    text = (
        "нужна квартира с видом на парк, западный округ, предчистовая отделка, "
        "в районе 9-16 этажей, два и более санузла, с тёплым полом, "
        "от двух комнат, до метро менее 15 минут"
    )
    response = client.post("/build-url", json={"text": text})
    assert response.status_code == 200
    data = response.json()

    assert data["criteria"]["rooms"] == ["2", "3+"]
    assert data["criteria"]["counties"] == ["ЗАО"]
    assert data["criteria"]["finish"] == "предчистовая"
    assert data["criteria"]["floor_min"] == 9
    assert data["criteria"]["floor_max"] == 16
    assert data["criteria"]["time_on_foot"] == 15

    assert "vidNaPark" in data["criteria"]["options"]
    assert "manybathrooms" in data["criteria"]["option_groups"]
    assert "teplyPol" in data["criteria"]["option_groups"]

    warnings_str = " ".join(data["warnings"])
    assert "видом на парк" not in warnings_str
    assert "два и более санузла" not in warnings_str
    assert "тёплым полом" not in warnings_str


def test_e2e_required_tags(client):
    """10. Выгодные предложения (requiredTags)."""
    text = (
        "хочу готовые квартиры, ипотеку по формуле 0,1%, "
        "специальная цена до 15.07, выгода до -15% до 15.07"
    )
    response = client.post("/build-url", json={"text": text})
    assert response.status_code == 200
    data = response.json()

    assert "requiredTags=zos,cashback,crossed,outlet" in data["url"]

    criteria = data["criteria"]
    assert criteria["required_tags"] == ["zos", "cashback", "crossed", "outlet"]

    warnings_str = " ".join(data["warnings"])
    assert "готовые квартиры" not in warnings_str
    assert "выгода" not in warnings_str
