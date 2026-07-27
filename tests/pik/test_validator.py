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


@pytest.mark.asyncio
async def test_validate_reports_finish_and_settlement_year_as_unverified():
    """D6: бэкенд ИГНОРИРУЕТ ``finish`` и ``settlementYear*`` — молчать нельзя.

    Живой замер (2026-07-27): ``blocks=477&rooms=1`` → 54;
    ``+settlementYearFrom=2030&settlementYearTo=2031`` → 54; ``+finish=0`` → 54.
    Контроль, что бэкенд не «сломан вообще»: ``blocks=411&timeOnFoot=12`` → 0.
    Значит result_count не учитывает 2 фильтра ссылки и выдавать его за
    полноценную проверку — то же нарушение, ради которого константа и заведена.
    """
    from app.parsing.schema import Finish

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 71})

    client = make_mock_client(handler)
    criteria = Criteria(
        rooms=[Rooms.ONE],
        finish=[Finish.READY],
        settlement_year_from=2026,
        settlement_year_to=2027,
    )

    result = await validate(criteria, client)

    assert result.result_count == 71
    assert result.warning is not None
    assert "отделку" in result.warning
    assert "год заселения" in result.warning
    # Контракт API не меняется: поле — про ЛОКАЦИИ, а их в запросе нет.
    assert result.location_filters_not_verified is False


@pytest.mark.asyncio
async def test_validate_stays_silent_when_all_filters_are_verifiable():
    """D6, контрпример: комнатность+цена бэкенд проверяет — предупреждать не о чем."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 42})

    client = make_mock_client(handler)
    criteria = Criteria(rooms=[Rooms.TWO], price_max=15_000_000)

    result = await validate(criteria, client)

    assert result.warning is None
    assert result.location_filters_not_verified is False


# ---------------------------------------------------------------------------
# Дефект №2: validate() пересекает гео-фолбэк, а не объединяет (та же логика,
# что build_url после фикса AI-23) — раньше расходились: build_url корректно
# показывал более узкое пересечение, а validate() объединял, и result_count
# завышался в разы (живой замер: 3889 вместо реальных 598).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_intersects_geo_fallback_same_as_build_url():
    """ЖК заведомо ЗА МКАД + `within_mkad=True` (внутри) → пересечение пусто —
    validate() и build_url() должны сойтись на ОДНОМ И ТОМ ЖЕ (более
    специфичном) списке blocks, и оба обязаны предупредить о непересечении."""
    from app.geo.candidates import complexes_in_mkad
    from app.parsing.schema import MatchedEntity
    from app.pik.url_builder import build_url

    outside = complexes_in_mkad(False)
    assert outside, "нет ЖК за МКАД — тест потерял смысл"
    chosen = outside[0]

    criteria = Criteria(within_mkad=True, complexes=[MatchedEntity(name="X", id=chosen)])

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"count": 5})

    client = make_mock_client(handler)
    build_url_warnings: list[str] = []
    url = build_url(criteria, build_url_warnings)

    result = await validate(criteria, client)

    assert f"blocks={chosen}" in url
    assert f"blocks={chosen}" in captured["url"]
    assert any("не пересекаются" in w for w in build_url_warnings)
    assert result.warning is not None
    assert "не пересекаются" in result.warning


@pytest.mark.asyncio
async def test_validate_forces_empty_blocks_when_landmark_match_empty():
    """Дефект №1 симметрично на validate(): considered-zero (``complexes_matched_empty``)
    + within_mkad=True → blocks остаётся пустым, а не откатывается на весь МКАД."""

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"count": 0})

    client = make_mock_client(handler)
    criteria = Criteria(within_mkad=True, complexes_matched_empty=True)

    result = await validate(criteria, client)

    assert "blocks=&" in captured["url"] or captured["url"].endswith("blocks=")
    assert result.warning is not None
    assert "не пересекаются" in result.warning
