import httpx
import pytest

from app.parsing.schema import Criteria, Rooms
from app.pik.validator import validate


def make_mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_validate_success_not_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://api.pik.ru/v2/filter?")
        # Проверяем, что параметры передаются корректно
        assert "rooms=2" in str(request.url)
        assert "priceTo=15000000" in str(request.url)
        return httpx.Response(200, json={"count": 42})

    client = make_mock_client(handler)
    criteria = Criteria(rooms=[Rooms.TWO], price_max=15000000)

    result = await validate(criteria, client)

    assert result.result_count == 42
    assert result.ok is True


@pytest.mark.asyncio
async def test_validate_success_empty():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 0})

    client = make_mock_client(handler)
    criteria = Criteria()

    result = await validate(criteria, client)

    assert result.result_count == 0
    assert result.ok is False


@pytest.mark.asyncio
async def test_validate_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Network is unreachable")

    client = make_mock_client(handler)
    criteria = Criteria()

    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True  # graceful: если проверить не удалось, считаем что ок
    assert result.warning == "выдача не проверена"


@pytest.mark.asyncio
async def test_validate_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Read timeout")

    client = make_mock_client(handler)
    criteria = Criteria()

    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True
    assert result.warning == "выдача не проверена"


@pytest.mark.asyncio
async def test_validate_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = make_mock_client(handler)
    criteria = Criteria()

    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True
    assert result.warning == "выдача не проверена"


@pytest.mark.asyncio
async def test_validate_invalid_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not a json")

    client = make_mock_client(handler)
    criteria = Criteria()

    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True
    assert result.warning == "выдача не проверена"
