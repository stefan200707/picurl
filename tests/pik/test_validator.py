"""Тесты для валидатора выдачи (промпт 08)."""

import httpx
import pytest

from app.parsing.schema import Criteria, Rooms
from app.pik.validator import validate

pytestmark = pytest.mark.asyncio

def make_client(
    payload: dict | list | str, status_code: int = 200, exception: Exception | None = None
) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.pik.ru"
        if exception:
            raise exception
        return httpx.Response(status_code, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))

async def test_validate_success_count():
    criteria = Criteria(rooms=[Rooms.TWO])
    client = make_client({"count": 42})
    result = await validate(criteria, client)

    assert result.result_count == 42
    assert result.ok is True

async def test_validate_success_items_list():
    criteria = Criteria()
    client = make_client({"items": [1, 2, 3]})
    result = await validate(criteria, client)

    assert result.result_count == 3
    assert result.ok is True

async def test_validate_empty_result():
    criteria = Criteria(price_max=10)
    client = make_client({"count": 0})
    result = await validate(criteria, client)

    assert result.result_count == 0
    assert result.ok is False

async def test_validate_network_error():
    criteria = Criteria()
    client = make_client({}, exception=httpx.ConnectError("Network is unreachable"))
    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True  # graceful fallback

async def test_validate_timeout():
    criteria = Criteria()
    client = make_client({}, exception=httpx.TimeoutException("Timeout"))
    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True

async def test_validate_unexpected_format():
    criteria = Criteria()
    client = make_client("Not a dict or list")
    result = await validate(criteria, client)

    assert result.result_count == 0
    assert result.ok is False

async def test_validate_http_error():
    criteria = Criteria()
    client = make_client({}, status_code=500)
    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True
