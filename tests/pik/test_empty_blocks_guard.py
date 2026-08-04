"""Д1: пустой ``blocks=`` не должен уезжать в ссылку.

Живой репродьюсер («…в районе метро Раменки (или Ломоносовский проспект)…»)
давал URL с `blocks=` без значения. На pik.ru это не «ноль ЖК», а СНЯТЫЙ
фильтр — выдаётся весь город, то есть исход ШИРЕ любого списка. При этом
warnings утверждали «показаны подходящие под часть».

Принятое решение по семантике (владелец проекта, 2026-08-04):

* пересечение пусто, а «более специфичный» список НЕПУСТ — как раньше:
  оставляем специфичный список + warning о непересечении (правило AND);
* пересечение пусто и специфичный список ПУСТ («посчитали ноль») — отдаём
  ``fallback.block_ids`` и честно говорим, что гео-требование, давшее ноль,
  применить не удалось. «Ноль» в параметре ``blocks`` невыразим, а список по
  метро/району строго уже, чем весь город;
* итоговый список пуст по любой причине — ключа ``blocks`` в query нет вовсе,
  и об этом сказано отдельной строкой.
"""

from urllib.parse import unquote

import httpx
import pytest

from app.parsing.schema import Criteria, MatchedEntity
from app.pik.location_fallback import (
    LOCATION_INTERSECTION_EMPTY_WARNING,
    LOCATION_NARROWING_NOT_APPLIED_WARNING,
    LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING,
    LocationFallback,
    combine_with_fallback,
)
from app.pik.url_builder import build_url

_REPRODUCER = (
    "Ищу 3-комнатную квартиру в районе метро Раменки (или Ломоносовский проспект), "
    "площадью от 75 м² с кухней-гостиной от 20 м². Этаж с 5 по 15, окна во двор или на парк. "
    "До метро не более 10 минут пешком, до парка и школы — до 10 минут. "
    "Готовая отделка (чистовая или под ключ), мастер-спальня с отдельным санузлом и второй "
    "гостевой санузел. Бюджет до 50 млн руб., не в брони, ключи до IV квартала 2027 года."
)


def _blocks_value(url: str) -> str | None:
    """Значение ``blocks`` в URL; ``None`` — ключа нет вовсе (это разные исходы)."""
    if "blocks=" not in url:
        return None
    return url.split("blocks=")[1].split("&")[0]


# --------------------------------------------------------------------------
# combine_with_fallback: три ветки семантики
# --------------------------------------------------------------------------


def test_counted_zero_falls_back_to_location_list():
    """«Посчитали ноль» + непустой фолбэк → список фолбэка, а не пустота."""
    warnings: list[str] = []
    fallback = LocationFallback(block_ids=["10", "11"])

    combined = combine_with_fallback([], fallback, Criteria(complexes_matched_empty=True), warnings)

    assert combined == ["10", "11"]
    assert LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING in warnings


def test_nonempty_specific_list_still_wins_over_fallback():
    """Правило AND не тронуто: непустой специфичный список переживает промах."""
    warnings: list[str] = []
    fallback = LocationFallback(block_ids=["10"])

    combined = combine_with_fallback(["7"], fallback, Criteria(), warnings)

    assert combined == ["7"]
    assert LOCATION_INTERSECTION_EMPTY_WARNING in warnings
    assert LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING not in warnings


def test_intersection_is_still_an_intersection():
    """Контроль предпосылки: непустое пересечение по-прежнему сужает."""
    warnings: list[str] = []
    fallback = LocationFallback(block_ids=["7", "10"])

    combined = combine_with_fallback(["7", "9"], fallback, Criteria(), warnings)

    assert combined == ["7"]
    assert warnings == []


# --------------------------------------------------------------------------
# build_url / validate: пустой список — не пустой параметр
# --------------------------------------------------------------------------


def test_build_url_omits_blocks_key_when_list_is_empty(monkeypatch):
    """Итоговый список пуст → ключа нет вовсе (пустой = «весь город») + warning.

    Через combine_with_fallback этот исход после правки недостижим, но защита
    обязана жить в самой точке записи параметра: молча отправить `blocks=` —
    это отдать пользователю ссылку ШИРЕ его запроса.
    """
    monkeypatch.setattr("app.pik.url_builder.combine_with_fallback", lambda *a, **k: [])
    warnings: list[str] = []

    url = build_url(Criteria(metro=[MatchedEntity(name="ВДНХ")]), warnings)

    assert _blocks_value(url) is None, url
    assert LOCATION_NARROWING_NOT_APPLIED_WARNING in warnings


def test_counted_zero_reaches_url_as_location_list():
    """Тот же исход на уровне ссылки: считаный ноль → ЖК по метро, не пустота."""
    warnings: list[str] = []
    criteria = Criteria(metro=[MatchedEntity(name="ВДНХ")], complexes_matched_empty=True)

    url = build_url(criteria, warnings)

    assert _blocks_value(url), url
    assert LOCATION_ZERO_MATCH_NOT_APPLIED_WARNING in warnings


@pytest.mark.asyncio
async def test_validate_sends_the_same_blocks_as_the_link():
    """Валидатор обязан проверять ровно то сужение, что получил пользователь.

    Расхождение build_url и validate уже однажды завышало result_count в 6.5
    раза, поэтому правка Д1 симметрична по обоим путям.
    """
    from app.pik.validator import validate

    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"count": 1})

    criteria = Criteria(metro=[MatchedEntity(name="ВДНХ")], complexes_matched_empty=True)
    url = build_url(criteria, [])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await validate(criteria, client)

    assert "blocks=&" not in captured["url"] and not captured["url"].endswith("blocks=")
    # Валидатор кодирует запятые (%2C), ссылка — нет: сравниваем множества id.
    sent = unquote(_blocks_value(captured["url"]) or "")
    assert sent.split(",") == (_blocks_value(url) or "").split(",")


# --------------------------------------------------------------------------
# Интеграция: весь запрос-репродьюсер целиком
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reproducer_link_has_real_blocks_and_no_false_warning(monkeypatch):
    """Полный путь parse → enrich (без ИИ) → build_url на живом репродьюсере."""
    from app.ai.enrichment import enrich, merge_enrichment
    from app.config import get_settings
    from app.parsing.parser import parse

    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "AI_ENRICHMENT_ENABLED", False)

    parsed = parse(_REPRODUCER)
    warnings = list(parsed.warnings)
    enrichment = await enrich(
        _REPRODUCER,
        parsed.criteria,
        warnings,
        pool=None,
        option_candidates=parsed.option_candidates,
    )
    criteria = merge_enrichment(parsed.criteria, enrichment)

    url = build_url(criteria, warnings)

    blocks = _blocks_value(url)
    assert blocks, f"гео-сужение пропало из ссылки: {url}"
    assert all(b for b in blocks.split(",")), f"пустой id в blocks: {blocks!r}"
    # Ложное утверждение из репродьюсера: ЖК «под часть условий» в ссылке не было.
    assert LOCATION_INTERSECTION_EMPTY_WARNING not in warnings
