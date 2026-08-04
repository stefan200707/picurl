import httpx
import pytest

from app.parsing.schema import Criteria, Rooms
from app.pik.validator import NOT_VALIDATED_WARNING, validate


def joined(result) -> str:
    """Склейка для проверок «этот факт где-то есть» — но не для «их два в одном»."""
    return " | ".join(result.warnings)


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
    assert result.warnings == [NOT_VALIDATED_WARNING]


@pytest.mark.asyncio
async def test_validate_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Read timeout")

    client = make_mock_client(handler)
    criteria = Criteria()

    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True
    assert result.warnings == [NOT_VALIDATED_WARNING]


@pytest.mark.asyncio
async def test_validate_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = make_mock_client(handler)
    criteria = Criteria()

    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True
    assert result.warnings == [NOT_VALIDATED_WARNING]


@pytest.mark.asyncio
async def test_validate_invalid_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not a json")

    client = make_mock_client(handler)
    criteria = Criteria()

    result = await validate(criteria, client)

    assert result.result_count is None
    assert result.ok is True
    assert result.warnings == [NOT_VALIDATED_WARNING]


# ---------------------------------------------------------------------------
# Г6: сетевой сбой СТИРАЛ предупреждения о непроверяемых фильтрах и сбрасывал
# ``location_filters_not_verified`` в False. Логика была перевёрнута: если
# проверку не удалось выполнить вовсе, непроверенным является ВСЁ, и
# предупреждение обосновано сильнее, а не слабее. Живой инцидент: в логе есть
# «выдача не проверена», result_count=null — и НЕТ строки про непроверенные
# локационные фильтры, хотя в URL был ``blocks=``; пользователь узнал только про
# второй из двух слоёв непроверенности.
# ---------------------------------------------------------------------------


def _criteria_with_metro_and_blocks() -> Criteria:
    """Форма из инцидента: метро (бэкенд игнорирует) + ЖК (``blocks=`` в URL)."""
    from app.parsing.schema import MatchedEntity

    return Criteria(
        rooms=[Rooms.TWO],
        metro=[MatchedEntity(name="Аэропорт Внуково", id="c0ffee00-0000-0000-0000-000000000001")],
        complexes=[MatchedEntity(name="Тестовый ЖК", id="477")],
    )


@pytest.mark.parametrize(
    "fail",
    [
        pytest.param(lambda _r: (_ for _ in ()).throw(httpx.ConnectError("unreachable")), id="net"),
        pytest.param(lambda _r: httpx.Response(500, text="Internal Server Error"), id="http-500"),
        pytest.param(lambda _r: httpx.Response(200, text="not a json"), id="bad-json"),
    ],
)
@pytest.mark.asyncio
async def test_validate_keeps_unverified_warnings_on_failure(fail):
    """Сбой проверки НЕ снимает предупреждения о непроверяемых фильтрах."""
    client = make_mock_client(fail)

    result = await validate(_criteria_with_metro_and_blocks(), client)

    assert result.result_count is None
    assert result.ok is True
    # 1) сам факт, что проверки не было — ОТДЕЛЬНЫМ элементом
    assert NOT_VALIDATED_WARNING in result.warnings
    # 2) отдельная строка про локационные фильтры — она НЕ должна исчезать
    assert any("метро/округу/району" in w for w in result.warnings)
    # 3) и это ДВА разных элемента, а не один склеенный: неделимый элемент
    #    невозможно разметить категорией (Г6).
    assert all(
        not ("выдача не проверена" in w and "метро/округу/району" in w) for w in result.warnings
    ), result.warnings
    # 4) структурное поле не сброшено
    assert result.location_filters_not_verified is True


@pytest.mark.asyncio
async def test_validate_failure_text_differs_from_success_text():
    """Два разных состояния должны читаться по-разному.

    «Проверка прошла, но бэкенд игнорирует эти параметры» и «проверки не было
    вовсе» — не одно и то же; раньше второе съедало первое целиком.
    """
    criteria = _criteria_with_metro_and_blocks()

    ok_client = make_mock_client(lambda _r: httpx.Response(200, json={"count": 12}))
    ok = await validate(criteria, ok_client)

    def fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    failed = await validate(criteria, make_mock_client(fail))

    assert ok.warnings and failed.warnings
    assert ok.warnings != failed.warnings
    ok_text, failed_text = joined(ok), joined(failed)
    # Успех говорит про result_count — он существует и чему-то равен.
    assert "result_count не учитывает" in ok_text
    assert "выдача не проверена" not in ok_text
    # Сбой не пересказывает result_count — его нет.
    assert "result_count не учитывает" not in failed_text
    assert NOT_VALIDATED_WARNING in failed.warnings


@pytest.mark.asyncio
async def test_validate_failure_names_the_check_that_was_actually_lost():
    """Акцент отказа — на blocks, а не на вечно непроверяемых параметрах.

    Прежний текст перечислял отделку и год — то, что не подтверждается НИКОГДА,
    даже при успешном ответе, — и молчал про ``blocks``, единственный фильтр,
    который бэкенд по нашим замерам реально проверяет. При отказе теряется ровно
    эта одна настоящая проверка, и назвать надо именно её.
    """
    from app.parsing.schema import Finish

    def fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    # Отделка ДВУМЯ значениями: одиночную бэкенд проверяет (hasFinish=1|2|3),
    # а список — нет, и именно она остаётся вечно непроверяемой.
    criteria = Criteria(rooms=[Rooms.ONE], finish=[Finish.READY, Finish.WHITE_BOX])
    result = await validate(criteria, make_mock_client(fail))

    loss = next(w for w in result.warnings if w.startswith("выдача не проверена"))
    assert "blocks" in loss
    # Потерянная проверка и справка о вечно непроверяемом — разные элементы.
    assert "отделку" not in loss
    assert any("отделку" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_validate_failure_without_unverified_filters_stays_terse():
    """Контроль: когда предупреждать не о чем, сбой даёт ровно одну строку."""

    def fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    result = await validate(Criteria(rooms=[Rooms.TWO]), make_mock_client(fail))

    assert result.warnings == [NOT_VALIDATED_WARNING]
    assert result.location_filters_not_verified is False


@pytest.mark.asyncio
async def test_validator_does_not_publish_geo_fallback_notes():
    """Заметки о том, КАК сузили, через валидатор не проходят вовсе.

    Они описывают критерии, а не ответ бэкенда, и принадлежат ``build_url``.
    Пока они шли отсюда, справка о сужении зависела от сетевого вызова, к
    которому не имеет отношения, и склеивалась с ним в один неделимый элемент.
    """

    def fail(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    criteria = Criteria(within_mkad=True, complexes_matched_empty=True)

    result = await validate(criteria, make_mock_client(fail))

    assert result.result_count is None
    assert NOT_VALIDATED_WARNING in result.warnings
    assert "не пересекаются" not in joined(result)
    assert "МКАД" not in joined(result)


def test_build_url_publishes_geo_fallback_notes_without_network():
    """Обратная половина: заметки доезжают, и сеть для этого не нужна."""
    from app.pik.url_builder import build_url

    warnings: list[str] = []
    build_url(Criteria(within_mkad=True), warnings)

    assert any("МКАД" in w for w in warnings), warnings
    # Каждая заметка — самостоятельный элемент, ни одна ни с чем не склеена.
    assert all("выдача не проверена" not in w for w in warnings)


@pytest.mark.asyncio
async def test_validate_reports_finish_and_settlement_year_as_unverified():
    """D6: срок заселения бэкенд ИГНОРИРУЕТ, список отделок не применяет.

    Живые замеры 2026-07-28 (baseline = 8191): ``settlementYearFrom=2030&
    settlementYearTo=2031`` → 8191; ``settlementMonthFrom=1&settlementMonthTo=2``
    → 8191; ``hasFinish=1,2`` → 8191 (а не 6791+1318). Контроль, что бэкенд не
    «сломан вообще»: ``timeOnFoot=5`` → 1985. Значит result_count не учитывает
    эти фильтры ссылки, и выдавать его за полноценную проверку — то же
    нарушение, ради которого константа и заведена.
    """
    from app.parsing.schema import Finish

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 71})

    client = make_mock_client(handler)
    criteria = Criteria(
        rooms=[Rooms.ONE],
        finish=[Finish.READY, Finish.WHITE_BOX],
        settlement_year_from=2026,
        settlement_year_to=2027,
    )

    result = await validate(criteria, client)

    assert result.result_count == 71
    # Оба ярлыка — в ОДНОМ элементе: это один факт «бэкенд игнорирует вот эти
    # параметры», перечисление внутри него дроблению не подлежит.
    assert any("отделку" in w and "срок заселения" in w for w in result.warnings)
    # Контракт API не меняется: поле — про ЛОКАЦИИ, а их в запросе нет.
    assert result.location_filters_not_verified is False


@pytest.mark.asyncio
async def test_validate_sends_has_finish_and_stays_silent_for_single_value():
    """Одиночная отделка 1|2|3 проверяема — уходит как ``hasFinish``, без warning.

    Дефект: валидатор слал ``finish=<...>``, а пользовательская ссылка —
    ``hasFinish``. Параметра ``finish`` бэкенд не знает (замер 2026-07-28:
    ``finish=2`` → 8191 = baseline), поэтому «непроверяемость отделки» была
    свойством нашей опечатки, а не потолком pik.ru: ``hasFinish=2`` → 1318.
    """
    from app.parsing.schema import Finish

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"count": 12})

    result = await validate(
        Criteria(rooms=[Rooms.ONE], finish=[Finish.WHITE_BOX]), make_mock_client(handler)
    )

    assert "hasFinish=2" in seen[0]
    assert "finish=2" not in seen[0].replace("hasFinish=2", "")
    assert all("отделку" not in w for w in result.warnings), result.warnings


@pytest.mark.asyncio
async def test_validate_keeps_finish_unverified_for_zero_and_for_lists():
    """Ноль и список остаются непроверяемыми — и в запрос не уходят вовсе.

    ``hasFinish=0`` → 8191 = baseline, ровно как заведомый мусор ``hasFinish=9``:
    бэкенд его не применяет. Список опаснее молчаливой бесполезности:
    ``hasFinish=0,1`` → 6791 (= как одиночная «1»), то есть отправка сузила бы
    count не тем фильтром, о котором просил пользователь.
    """
    from app.parsing.schema import Finish

    for finish in ([Finish.NONE], [Finish.NONE, Finish.READY], [Finish.READY, Finish.FURNISHED]):
        seen: list[str] = []

        def handler(request: httpx.Request, seen: list[str] = seen) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, json={"count": 12})

        result = await validate(Criteria(finish=finish), make_mock_client(handler))

        assert "hasFinish" not in seen[0], finish
        assert any("отделку" in w for w in result.warnings), (finish, result.warnings)


@pytest.mark.asyncio
async def test_validate_stays_silent_when_all_filters_are_verifiable():
    """D6, контрпример: комнатность+цена бэкенд проверяет — предупреждать не о чем."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 42})

    client = make_mock_client(handler)
    criteria = Criteria(rooms=[Rooms.TWO], price_max=15_000_000)

    result = await validate(criteria, client)

    assert result.warnings == []
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
    # Предупреждает ОДИН раз и ровно тот, кто сузил. Валидатор ту же логику
    # по-прежнему прогоняет (иначе result_count проверял бы не то сужение), но
    # предупреждение больше не дублирует — раньше оно уходило в ответ дважды,
    # и склейка это маскировала.
    assert [w for w in build_url_warnings if "не пересекаются" in w] != []
    assert "не пересекаются" not in joined(result)


@pytest.mark.asyncio
async def test_validate_matches_link_when_landmark_match_empty():
    """Дефект №1 симметрично на validate(), в редакции Д1 (2026-08-04).

    Замысел теста прежний: считаный ноль (``complexes_matched_empty``) не должен
    превращаться в «фильтра нет». Изменилось, ЧТО этим является. Раньше тест
    пиннил пустой ``blocks=``, считая его выражением нуля; живой замер показал
    обратное — пустое значение на pik.ru СНИМАЕТ фильтр и отдаёт весь город, то
    есть ровно тот исход, от которого тест защищал, только шире МКАД-списка.

    Поэтому теперь проверяется суть: (1) фильтр по ЖК в проверочном запросе
    ЕСТЬ и он непустой; (2) валидатор шлёт РОВНО то же сужение, что уехало в
    ссылку (расхождение build_url и validate однажды завышало result_count в
    6.5 раза); (3) о неприменённом гео-требовании сказано, и сказано ОДИН раз —
    тем, кто сузил, то есть build_url, а не валидатором.
    """
    from urllib.parse import unquote

    from app.pik.location_fallback import LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING
    from app.pik.url_builder import build_url

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"count": 0})

    client = make_mock_client(handler)
    criteria = Criteria(within_mkad=True, complexes_matched_empty=True)

    result = await validate(criteria, client)

    sent = unquote(captured["url"]).split("blocks=")[1].split("&")[0]
    assert sent, "считаный ноль превратился в «фильтра нет» — это и был дефект"

    build_url_warnings: list[str] = []
    url = build_url(criteria, build_url_warnings)
    assert sent.split(",") == url.split("blocks=")[1].split("&")[0].split(",")

    assert LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING in build_url_warnings
    assert LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING not in joined(result)
