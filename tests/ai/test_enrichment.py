from unittest.mock import AsyncMock, patch

import pytest

from app.ai.enrichment import (
    enrich,
    merge_enrichment,
    resolve_options,
    sanitize_against_shortlist,
    sanitize_option_resolution,
)
from app.ai.schema import (
    AIEnrichmentAnswer,
    ComplexCandidate,
    OptionMatch,
    OptionResolutionAnswer,
)
from app.config import get_settings
from app.geo.poi import POICategory
from app.parsing.schema import (
    Criteria,
    LandmarkRequirement,
    POIRequirement,
    StationClassRequirement,
)

#: Координаты МГУ, как в app/reference/landmarks.json (используются и в
#: tests/geo/test_candidates.py) — без явной дистанции, как в живом запросе
#: «однушка рядом с МГУ подешевле» из бага AI-12.
_MGU = LandmarkRequirement(name="МГУ им. Ломоносова", lat=55.703326, lon=37.530762)


@pytest.fixture
def mock_settings():
    settings = get_settings()
    original_enabled = settings.AI_ENRICHMENT_ENABLED
    original_key_claude = settings.ANTHROPIC_API_KEY
    original_cli_path = settings.ANTIGRAVITY_CLI_PATH
    settings.AI_ENRICHMENT_ENABLED = True
    settings.ANTHROPIC_API_KEY = "sk-test"
    settings.ANTIGRAVITY_CLI_PATH = "agy"
    yield settings
    settings.AI_ENRICHMENT_ENABLED = original_enabled
    settings.ANTHROPIC_API_KEY = original_key_claude
    settings.ANTIGRAVITY_CLI_PATH = original_cli_path


@pytest.mark.asyncio
@patch("app.ai.enrichment.lookup_semantic", return_value=None)
@patch(
    "app.ai.enrichment.build_candidate_shortlist",
    return_value=[
        ComplexCandidate(
            id="1",
            name="ЖК",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
        )
    ],
)
async def test_enrich_disabled(mock_build, mock_lookup, mock_settings):
    mock_settings.AI_ENRICHMENT_ENABLED = False

    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )
    warnings = []

    result = await enrich("хочу со школой", criteria, warnings)

    assert result.ai_used is False
    assert result.success is False
    assert "ИИ-обогащение выключено — часть запроса не обработана" in warnings


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.call_model")
@patch("app.ai.enrichment.lookup_semantic", return_value=None)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_logs_ai_call_row(mock_build, mock_lookup, mock_call, mock_log, mock_settings):
    """Вызов enrich() с реальным обращением к ИИ пишет строку ai_call_log."""
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="ЖК",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
        )
    ]
    mock_call.return_value = AIEnrichmentAnswer(
        matched_complex_ids=["1"],
        center_district_ids=[],
        poi_findings={},
        explanation="x",
        confidence=0.9,
    )
    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )

    await enrich("хочу со школой", criteria, [], pool=None)

    mock_log.assert_awaited_once()
    fields = mock_log.await_args.kwargs
    assert fields["had_poi_or_center"] is True
    assert fields["fully_resolved_deterministically"] is False
    assert fields["cache_hit"] is False
    assert fields["ai_called"] is True
    # ИИ вернул matched_complex_ids, которых не даёт детерминированный слой.
    assert fields["criteria_changed_by_ai"] is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch(
    "app.ai.enrichment.build_candidate_shortlist",
    return_value=[
        ComplexCandidate(
            id="1", name="ЖК", district=None, county=None, metro=[], is_center=None, known_poi={}
        )
    ],
)
async def test_enrich_logs_when_disabled(mock_build, mock_log, mock_settings):
    """Даже при выключенном ИИ пишется строка ai_call_log (ai_called=False)."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )

    await enrich("хочу со школой", criteria, [], pool=None)

    mock_log.assert_awaited_once()
    fields = mock_log.await_args.kwargs
    assert fields["had_poi_or_center"] is True
    assert fields["ai_called"] is False
    assert fields["cache_hit"] is False
    assert fields["criteria_changed_by_ai"] is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist", return_value=[])
async def test_enrich_logs_when_no_candidates(mock_build, mock_log, mock_settings):
    """Пустой шорт-лист тоже логируется (одна строка на каждый вызов enrich).

    Критерии с poi_requirements гарантированно проходят гейт 1 (Milestone
    AI-14) и реально доходят до build_candidate_shortlist — иначе (с «пустыми»
    критериями) enrich() короткозамкнётся раньше, и этот тест перестанет
    проверять то, что заявлено в его имени/докстринге (см.
    test_enrich_gate1_blocks_when_nothing_to_enrich для проверки самого гейта).
    """
    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )

    await enrich("что-то невнятное со школой", criteria, [], pool=None)

    mock_build.assert_called_once()
    mock_log.assert_awaited_once()
    fields = mock_log.await_args.kwargs
    assert fields["had_poi_or_center"] is True
    assert fields["ai_called"] is False


# --- Гейт 1 (Milestone AI-14): возврат fallback-гейта из enrich() -----------
#
# До этой правки гейт 1 был закомментирован (Milestone AI-11), из-за чего
# enrich() дёргал ИИ-путь (шорт-лист/кэш/модель) на КАЖДЫЙ запрос — даже когда
# обогащать нечего (нет poi_requirements/center_requested/option_candidates).
# Живой прогон показал два бесполезных вызова ИИ на таком запросе, оба упали в
# 429 (общая квота OAuth-сессии). Гейт 2 (fully_resolved_deterministically)
# остаётся выключенным — численный критерий возврата (N >= 500, порог 90%) по
# CLAUDE.md ещё не подтверждён на данных.


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.call_model")
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_gate1_blocks_when_nothing_to_enrich(
    mock_build, mock_call, mock_log, mock_settings
):
    """Нет ни poi_requirements, ни center_requested, ни option_candidates —
    ИИ-путь не запускается вовсе (ни шорт-лист, ни модель), но строка в
    ai_call_log всё равно пишется (наблюдаемость не должна зависеть от гейта)."""
    criteria = Criteria()

    result = await enrich("двушка у метро, до 15 млн, с отделкой", criteria, [], pool=None)

    mock_build.assert_not_called()
    mock_call.assert_not_called()
    assert result.ai_used is False
    assert result.ai_failed is False
    assert result.success is True

    mock_log.assert_awaited_once()
    fields = mock_log.await_args.kwargs
    assert fields["had_poi_or_center"] is False
    assert fields["ai_called"] is False
    assert fields["cache_hit"] is False
    assert fields["criteria_changed_by_ai"] is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
async def test_enrich_gate1_preserves_existing_warnings(mock_log, mock_settings):
    """Гейт 1 не должен стирать уже накопленные warnings нижних слоёв (инвариант
    «ничего не отбрасывается молча» касается и самого гейта)."""
    criteria = Criteria()
    warnings = ["«тарабарщина»: не удалось распознать, не попало в ссылку"]

    await enrich("хочу тарабарщину", criteria, warnings, pool=None)

    assert warnings == ["«тарабарщина»: не удалось распознать, не попало в ссылку"]


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist", return_value=[])
async def test_enrich_gate1_allows_when_poi_present(mock_build, mock_log, mock_settings):
    """poi_requirements — гейт 1 пропускает запрос дальше (в шорт-лист)."""
    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )

    await enrich("хочу со школой", criteria, [], pool=None)

    mock_build.assert_called_once()


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist", return_value=[])
async def test_enrich_gate1_allows_when_center_requested(mock_build, mock_log, mock_settings):
    """center_requested — гейт 1 пропускает запрос дальше (в шорт-лист)."""
    criteria = Criteria(center_requested=True)

    await enrich("хочу в центре", criteria, [], pool=None)

    mock_build.assert_called_once()


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.call_option_resolver")
@patch("app.ai.enrichment.build_candidate_shortlist", return_value=[])
async def test_enrich_gate1_allows_when_only_option_candidates_present(
    mock_build, mock_resolver, mock_log, mock_settings
):
    """Непустые option_candidates тоже приоткрывают гейт 1 (промпт 25): даже без
    poi/center основной ИИ-путь (шорт-лист) не короткозамыкается раньше срока —
    условие гейта обязано учитывать все три признака «есть что обогащать»."""
    mock_resolver.return_value = OptionResolutionAnswer(matches=[])
    criteria = Criteria()

    await enrich(
        "квартира с отдельным санузлом",
        criteria,
        [],
        pool=None,
        option_candidates=["отдельным санузлом"],
    )

    mock_build.assert_called_once()


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist", return_value=[])
async def test_enrich_gate1_allows_landmark_only_request(mock_build, mock_log, mock_settings):
    """landmark_requirements — единственное осознанное исключение из буквального
    условия гейта 1: сужение по ориентиру (Milestone AI-13, регрессия AI-12)
    работает отдельной, всегда включённой веткой ниже по коду и само не зовёт
    ИИ. Если бы гейт 1 не пропускал landmark-only запросы дальше, они бы молча
    схлопывались в noop() до этой ветки — именно баг AI-12, воспроизведённый
    падением test_enrich_resolves_landmark_* при попытке не сделать это
    исключение (см. комментарий у гейта в app/ai/enrichment.py)."""
    criteria = Criteria(landmark_requirements=[_MGU])

    await enrich("рядом с мгу", criteria, [], pool=None)

    mock_build.assert_called_once()


@pytest.mark.asyncio
async def test_log_ai_call_writes_row_to_pool():
    """log_ai_call выполняет INSERT в ai_call_log с переданными полями (мок БД)."""
    from app.ai.memory import log_ai_call

    pool = AsyncMock()
    await log_ai_call(
        pool,
        had_poi_or_center=True,
        fully_resolved_deterministically=False,
        cache_hit=False,
        ai_called=True,
        criteria_changed_by_ai=True,
    )

    pool.execute.assert_awaited_once()
    args = pool.execute.await_args.args
    assert "ai_call_log" in args[0]
    # (query, had_poi_or_center, fully_resolved, cache_hit, ai_called, changed)
    assert args[1:] == (True, False, False, True, True)


@pytest.mark.asyncio
async def test_log_ai_call_noop_without_pool():
    """pool=None — наблюдаемость best-effort, ничего не пишем и не падаем."""
    from app.ai.memory import log_ai_call

    await log_ai_call(
        None,
        had_poi_or_center=True,
        fully_resolved_deterministically=True,
        cache_hit=False,
        ai_called=False,
        criteria_changed_by_ai=False,
    )


def test_sanitize_against_shortlist():
    candidates = [
        ComplexCandidate(
            id="valid1",
            name="ЖК 1",
            district="dist_valid",
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
        ),
        ComplexCandidate(
            id="valid2",
            name="ЖК 2",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
        ),
    ]

    answer = AIEnrichmentAnswer(
        matched_complex_ids=["valid1", "invalid3"],
        center_district_ids=["dist_valid", "dist_invalid"],
        poi_findings={"valid1": {"school": True}, "invalid3": {"school": True}},
        explanation="test",
        confidence=0.9,
    )

    sanitized = sanitize_against_shortlist(answer, candidates)
    assert "valid1" in sanitized.matched_complex_ids
    assert "invalid3" not in sanitized.matched_complex_ids
    assert "dist_valid" in sanitized.center_district_ids
    assert "dist_invalid" not in sanitized.center_district_ids
    assert "valid1" in sanitized.poi_findings
    assert "invalid3" not in sanitized.poi_findings


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_model")
@patch("app.ai.enrichment.lookup_semantic", return_value=None)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_api_error(mock_build, mock_lookup, mock_call, mock_settings):
    mock_call.side_effect = ValueError("Model did not return tool use block")
    mock_build.return_value = [
        ComplexCandidate(
            id="valid1",
            name="ЖК 1",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
        )
    ]

    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )
    warnings = []

    result = await enrich("хочу со школой", criteria, warnings)

    # Честный ai_used (правка "честный ai_used"): попытка провалилась, ИИ ни на
    # что не повлиял — ai_used=False, а не True. Сам факт неудачной попытки не
    # теряется — он в отдельном поле ai_failed.
    assert result.ai_used is False
    assert result.ai_failed is True
    assert result.success is False
    assert "не удалось обработать ИИ-обогащение (ошибка сервиса)" in warnings
    # AIMeta (то, что реально уходит в ответ API) прокидывает оба поля.
    assert result.meta.ai_used is False
    assert result.meta.ai_failed is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.build_candidate_shortlist", return_value=[])
@patch("app.ai.enrichment.call_option_resolver")
async def test_resolve_options_applied_to_criteria(mock_resolver, _mock_build, mock_settings):
    """«отдельный санузел» не сматчил rapidfuzz → фрагмент уходит в модель →
    ответ (manybathrooms) применяется к criteria как группа опций."""
    mock_resolver.return_value = OptionResolutionAnswer(
        matches=[OptionMatch(phrase="отдельным санузлом", slug="manybathrooms")]
    )

    criteria = Criteria()
    warnings = ["«отдельным санузлом»: не удалось распознать, не попало в ссылку"]

    await enrich(
        "квартира с отдельным санузлом",
        criteria,
        warnings,
        option_candidates=["отдельным санузлом"],
    )

    # Slug применён так же, как если бы его сматчил rapidfuzz.
    assert "manybathrooms" in criteria.option_groups
    # Фраза больше не висит как нераспознанная.
    assert not any("отдельным санузлом" in w for w in warnings)
    # Фрагменты реально ушли в контекст модели.
    context = mock_resolver.call_args.args[1]
    assert "отдельным санузлом" in context["fragments"]
    # Полный список опций/групп уходит в модель (не шорт-лист).
    assert any(o["slug"] == "manybathrooms" for o in context["option_groups"])


@pytest.mark.asyncio
@patch("app.ai.enrichment.build_candidate_shortlist", return_value=[])
@patch("app.ai.enrichment.call_option_resolver")
async def test_resolve_options_writes_to_options_list(mock_resolver, _mock_build, mock_settings):
    """slug из options.json применяется именно к criteria.options."""
    mock_resolver.return_value = OptionResolutionAnswer(
        matches=[OptionMatch(phrase="окна выходят в город", slug="vidNaGorod")]
    )
    criteria = Criteria()
    warnings: list[str] = []

    await enrich(
        "квартира где окна выходят в город",
        criteria,
        warnings,
        option_candidates=["окна выходят в город"],
    )

    assert "vidNaGorod" in criteria.options


@pytest.mark.asyncio
@patch("app.ai.enrichment.build_candidate_shortlist", return_value=[])
@patch("app.ai.enrichment.call_option_resolver")
async def test_resolve_options_logs_alias(mock_resolver, _mock_build, mock_settings):
    """Сопоставление «фраза → slug» логируется в карту памяти как option_alias."""
    mock_resolver.return_value = OptionResolutionAnswer(
        matches=[OptionMatch(phrase="отдельным санузлом", slug="manybathrooms", confidence=0.7)]
    )
    criteria = Criteria()
    pool = AsyncMock()

    with patch("app.ai.enrichment.store_structured_fact") as mock_store:
        mock_store.return_value = None
        await resolve_options(criteria, ["отдельным санузлом"], [], pool)

    mock_store.assert_awaited_once()
    args = mock_store.await_args.args
    # (pool, subject_type, subject_id=slug, fact_type, value, source, confidence)
    assert args[1] == "option_group"
    assert args[2] == "manybathrooms"
    assert args[3] == "option_alias"
    assert args[4] == {"phrase": "отдельным санузлом"}


def test_sanitize_option_resolution_rejects_hallucination():
    """slug вне справочника отбрасывается, null-фразы игнорируются."""
    answer = OptionResolutionAnswer(
        matches=[
            OptionMatch(phrase="отдельным санузлом", slug="manybathrooms"),
            OptionMatch(phrase="что-то", slug="totally_made_up_slug"),
            OptionMatch(phrase="без совпадения", slug=None),
        ]
    )
    resolved = sanitize_option_resolution(answer)
    slugs = {slug for _phrase, slug, _stype, _conf in resolved}
    assert slugs == {"manybathrooms"}


@pytest.mark.asyncio
@patch("app.ai.enrichment.build_candidate_shortlist", return_value=[])
@patch("app.ai.enrichment.call_option_resolver")
async def test_resolve_options_disabled_keeps_warning(mock_resolver, _mock_build, mock_settings):
    """При выключенном ИИ фрагмент остаётся в warnings, модель не дёргается."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    criteria = Criteria()
    warnings = ["«отдельным санузлом»: не удалось распознать, не попало в ссылку"]

    await resolve_options(criteria, ["отдельным санузлом"], warnings, None)

    mock_resolver.assert_not_called()
    assert criteria.option_groups == []
    assert any("отдельным санузлом" in w for w in warnings)


# --- Регрессия AI-12: сужение complexes по ориентиру («рядом с МГУ») --------
#
# Баг (живой прогон): «однушка рядом с МГУ подешевле» распознавала ориентир
# (координаты есть), но complexes не сужались (в URL нет blocks=...) и
# warnings были пустыми — пользователь не предупреждён. Причина: сужение шло
# только через матч POI/центра, а landmark_requirements не учитывался вовсе,
# плюс единственный путь применения (гейт 2) выключен по Milestone AI-11.
# Здесь — отдельный, всегда включённый детерминированный шаг именно для
# ориентиров (чистая математика haversine, не работа ИИ).


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_resolves_landmark_deterministically_when_ai_disabled(
    mock_build, mock_log, mock_settings
):
    """«Рядом с ориентиром» сужает complexes без ИИ, даже когда ИИ выключен."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="У МГУ",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
            lat=55.7050,
            lon=37.5320,
        ),
        ComplexCandidate(
            id="2",
            name="Далеко",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
            lat=55.9000,
            lon=37.4000,
        ),
    ]
    criteria = Criteria(landmark_requirements=[_MGU])
    warnings = []

    result = await enrich("однушка рядом с мгу подешевле", criteria, warnings)

    assert result.ai_used is False
    assert result.success is True
    assert result.matched_complex_ids == ["1"]
    # ИИ тут не при чём — не должно быть даже гейт-1 warning'а «ИИ выключено».
    assert not any("выключено" in w for w in warnings)


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_resolves_landmark_even_when_ai_enabled(mock_build, mock_log, mock_settings):
    """Сужение по ориентиру не идёт в модель, даже когда ИИ включён и доступен
    (это чистая математика, а не задача, требующая рассуждения ИИ)."""
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="У МГУ",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
            lat=55.7050,
            lon=37.5320,
        ),
    ]
    criteria = Criteria(landmark_requirements=[_MGU])

    with patch("app.ai.enrichment.call_model") as mock_call:
        result = await enrich("рядом с мгу", criteria, [])
        mock_call.assert_not_called()

    assert result.matched_complex_ids == ["1"]
    assert result.ai_used is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_landmark_warns_when_no_coordinates(mock_build, mock_log, mock_settings):
    """Если у ЖК-кандидатов вообще нет координат — сужение физически
    невозможно; честный warning вместо тишины (инвариант «ничего не
    отбрасывается молча»), а не имитация работы."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="ЖК без координат",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
        ),
    ]
    criteria = Criteria(landmark_requirements=[_MGU])
    warnings = []

    result = await enrich("рядом с мгу", criteria, warnings)

    assert result.matched_complex_ids == []
    assert any("нет координат" in w for w in warnings)


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_landmark_warns_when_nothing_nearby(mock_build, mock_log, mock_settings):
    """Координаты есть, но в радиусе «рядом» ничего не найдено — предупреждаем,
    а не оставляем criteria молча ненасыщенными (иначе URL покажет весь город,
    и пользователь решит, что фильтр применился)."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="Далеко",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
            lat=55.9000,
            lon=37.4000,
        ),
    ]
    criteria = Criteria(landmark_requirements=[_MGU])
    warnings = []

    result = await enrich("рядом с мгу", criteria, warnings)

    assert result.matched_complex_ids == []
    assert any("не найдено" in w for w in warnings)


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.call_model")
@patch("app.ai.enrichment.lookup_semantic", return_value=None)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_landmark_combined_with_poi_still_uses_ai_path(
    mock_build, mock_lookup, mock_call, mock_log, mock_settings
):
    """Landmark + POI вместе — короткого замыкания нет (POI всё ещё требует
    ИИ), но кандидаты для ИИ уже учитывают расстояние до ориентира."""
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="У МГУ",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={"school": True},
            lat=55.7050,
            lon=37.5320,
        ),
    ]
    mock_call.return_value = AIEnrichmentAnswer(
        matched_complex_ids=["1"],
        center_district_ids=[],
        poi_findings={},
        explanation="x",
        confidence=0.9,
    )
    criteria = Criteria(
        landmark_requirements=[_MGU],
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")],
    )

    result = await enrich("рядом с мгу со школой", criteria, [])

    mock_call.assert_called_once()
    assert result.ai_used is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_landmark_result_merges_into_criteria_complexes(
    mock_build, mock_log, mock_settings
):
    """merge_enrichment применяет результат так же, как если бы его вернула
    модель — итоговая проверка (fixes AI-12): URL получает сужение по ЖК."""
    from app.parsing.schema import MatchedEntity
    from app.reference.loader import RefEntry

    mock_settings.AI_ENRICHMENT_ENABLED = False
    mock_build.return_value = [
        ComplexCandidate(
            id="42",
            name="У МГУ",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
            lat=55.7050,
            lon=37.5320,
        ),
    ]
    criteria = Criteria(landmark_requirements=[_MGU])
    warnings = []

    result = await enrich("рядом с мгу", criteria, warnings)

    with patch(
        "app.reference.loader.load_complexes",
        return_value=[RefEntry(name="У МГУ", slug="near-mgu", id="42")],
    ):
        criteria = merge_enrichment(criteria, result)

    assert criteria.complexes == [MatchedEntity(name="У МГУ", slug="near-mgu", id="42")]


# --- Класс станций «любая станция линии» (Milestone AI-15) ------------------
#
# «Нужна двушка рядом с МЦД не важно какой станции, до 15 млн» — обобщение
# ориентиров (см. регрессию AI-12 выше) на КЛАСС точек: чистая математика
# (haversine до БЛИЖАЙШЕЙ станции подходящего класса), отдельная всегда
# включённая ветка, не гейт 1/2.

_MCD = StationClassRequirement(line_prefix="МЦД")


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_gate1_allows_station_class_only_request(mock_build, mock_log, mock_settings):
    """station_class_requirements — то же обязательное исключение из гейта 1,
    что и landmark_requirements: своя ветка ниже сама не зовёт ИИ."""
    mock_build.return_value = []
    criteria = Criteria(station_class_requirements=[_MCD])

    await enrich("рядом с мцд не важно какой станции", criteria, [], pool=None)

    mock_build.assert_called_once()


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.station_class_points")
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_resolves_station_class_deterministically_when_ai_disabled(
    mock_build, mock_points, mock_log, mock_settings
):
    """«Рядом с МЦД не важно какой станции» сужает complexes без ИИ, даже
    когда ИИ выключен."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    mock_points.return_value = [(55.8000, 37.6000)]
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="У станции",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
            lat=55.8009,
            lon=37.6000,
        ),
        ComplexCandidate(
            id="2",
            name="Далеко",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
            lat=56.2000,
            lon=37.0000,
        ),
    ]
    criteria = Criteria(station_class_requirements=[_MCD])
    warnings = []

    result = await enrich("двушка рядом с мцд не важно какой станции", criteria, warnings)

    assert result.ai_used is False
    assert result.success is True
    assert result.matched_complex_ids == ["1"]
    assert not any("выключено" in w for w in warnings)


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.station_class_points", return_value=[])
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_station_class_warns_when_no_station_coordinates(
    mock_build, mock_points, mock_log, mock_settings
):
    """Ни у одной станции подходящего класса нет координат (metro.json ещё не
    обогащён) — честный warning вместо тишины."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="ЖК",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
            lat=55.8009,
            lon=37.6000,
        ),
    ]
    criteria = Criteria(station_class_requirements=[_MCD])
    warnings = []

    result = await enrich("рядом с мцд не важно какой станции", criteria, warnings)

    assert result.matched_complex_ids == []
    assert any("в справочнике метро нет координат" in w for w in warnings)


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.station_class_points", return_value=[(55.8000, 37.6000)])
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_station_class_warns_when_no_complex_coordinates(
    mock_build, mock_points, mock_log, mock_settings
):
    """Станции класса известны, но ни у одного ЖК-кандидата нет координат —
    сужение физически невозможно, честный warning."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="ЖК без координат",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
        ),
    ]
    criteria = Criteria(station_class_requirements=[_MCD])
    warnings = []

    result = await enrich("рядом с мцд не важно какой станции", criteria, warnings)

    assert result.matched_complex_ids == []
    assert any("в справочнике ЖК нет координат" in w for w in warnings)


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.station_class_nearest_fallback", return_value=([], None))
@patch("app.ai.enrichment.station_class_points", return_value=[(55.8000, 37.6000)])
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_station_class_warns_when_nothing_nearby_and_fallback_unavailable(
    mock_build, mock_points, mock_fallback, mock_log, mock_settings
):
    """Координаты есть, но в радиусе «рядом» ничего не найдено, а фолбэк
    (Milestone AI-18) сам ничего предложить не смог (например, явная дистанция
    от пользователя не позволяет деградировать) — сохраняется прежний честный
    warning, пустой результат остаётся пустым, а не подменяется молча."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    mock_build.return_value = [
        ComplexCandidate(
            id="1",
            name="Далеко",
            district=None,
            county=None,
            metro=[],
            is_center=None,
            known_poi={},
            lat=56.2000,
            lon=37.0000,
        ),
    ]
    criteria = Criteria(station_class_requirements=[_MCD])
    warnings = []

    result = await enrich("рядом с мцд не важно какой станции", criteria, warnings)

    assert result.matched_complex_ids == []
    assert any("не найдено" in w for w in warnings)
    assert not any("показаны ближайшие" in w for w in warnings)


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.station_class_nearest_fallback")
@patch("app.ai.enrichment.station_class_points", return_value=[(55.8000, 37.6000)])
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_enrich_station_class_falls_back_to_nearest_when_default_radius_empty(
    mock_build, mock_points, mock_fallback, mock_log, mock_settings
):
    """Milestone AI-18: если в радиусе по умолчанию пусто, вместо тишины
    задействуется фолбэк на ближайшие ЖК (осмысленная деградация вместо пустой
    выдачи — живой баг «в районе коричневой ветки»/«на кольце»)."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    far_candidate = ComplexCandidate(
        id="1",
        name="Далеко",
        district=None,
        county=None,
        metro=[],
        is_center=None,
        known_poi={},
        lat=56.2000,
        lon=37.0000,
    )
    mock_build.return_value = [far_candidate]
    mock_fallback.return_value = (
        [far_candidate],
        "в радиусе 1.5 км от станций класса «МЦД» ЖК нет; показаны ближайшие — от 44.30 км",
    )
    criteria = Criteria(station_class_requirements=[_MCD])
    warnings = []

    result = await enrich("рядом с мцд не важно какой станции", criteria, warnings)

    assert result.matched_complex_ids == ["1"]
    assert any("показаны ближайшие" in w for w in warnings)
    mock_fallback.assert_called_once()
