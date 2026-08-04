"""Г2: ноль от модели — не то же самое, что ноль от математики.

``complexes_matched=True`` означает «сужение реально считалось». Для
детерминированных веток (ориентир/POI/центр) это правда: пустой результат там —
следствие haversine, и пересечение с ним обязано давать пусто. Для ответа
модели это НЕ так: «я не нашёл подходящих среди присланных» — суждение, а не
расчёт, и оно затирало посчитанный гео-фолбэк.

Цена ошибки асимметрична. Пустой ``blocks=`` НЕ сужает выдачу — он убирает
фильтр целиком, и пользователь получает весь город. То есть «сузили до нуля по
мнению модели» на деле означает «показали всё», причём вместо честно
посчитанных ближайших ЖК. Поэтому пустой ответ модели не применяется как
сужение никогда.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.ai.enrichment import AI_NO_MATCH_WARNING, enrich, merge_enrichment
from app.ai.schema import AIEnrichmentAnswer, ComplexCandidate
from app.config import get_settings
from app.geo.poi import POICategory
from app.parsing.schema import Criteria, MatchedEntity, POIRequirement


@pytest.fixture
def mock_settings():
    settings = get_settings()
    original_enabled = settings.AI_ENRICHMENT_ENABLED
    original_key = settings.ANTHROPIC_API_KEY
    settings.AI_ENRICHMENT_ENABLED = True
    settings.ANTHROPIC_API_KEY = "sk-test"
    yield settings
    settings.AI_ENRICHMENT_ENABLED = original_enabled
    settings.ANTHROPIC_API_KEY = original_key


def _candidate(cid: str = "1") -> ComplexCandidate:
    return ComplexCandidate(
        id=cid,
        name=f"ЖК {cid}",
        district=None,
        county=None,
        metro=[],
        is_center=None,
        known_poi={},
    )


def _poi_criteria() -> Criteria:
    return Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.call_model")
@patch("app.ai.enrichment.lookup_semantic", return_value=None)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_empty_ai_match_is_not_treated_as_narrowing(
    mock_build, mock_lookup, mock_call, mock_log, mock_settings
) -> None:
    mock_build.return_value = [_candidate()]
    mock_call.return_value = AIEnrichmentAnswer(
        matched_complex_ids=[],
        center_district_ids=[],
        poi_findings={},
        explanation="ни один кандидат не подошёл",
        confidence=0.9,
    )
    warnings: list[str] = []

    result = await enrich("хочу со школой", _poi_criteria(), warnings, pool=None)

    assert result.complexes_matched is False
    assert AI_NO_MATCH_WARNING in warnings


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.call_model")
@patch("app.ai.enrichment.lookup_semantic", return_value=None)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_empty_ai_match_leaves_deterministic_complexes_intact(
    mock_build, mock_lookup, mock_call, mock_log, mock_settings
) -> None:
    """Ключевой сценарий: посчитанные ЖК переживают пустой ответ модели."""
    mock_build.return_value = [_candidate()]
    mock_call.return_value = AIEnrichmentAnswer(
        matched_complex_ids=[],
        center_district_ids=[],
        poi_findings={},
        explanation="",
        confidence=0.5,
    )
    criteria = _poi_criteria()
    criteria.complexes = [MatchedEntity(name="Нарвин", slug="narvin", id="477")]

    result = await enrich("хочу со школой", criteria, [], pool=None)
    merged = merge_enrichment(criteria, result)

    assert [c.id for c in merged.complexes] == ["477"]
    assert merged.complexes_matched_empty is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.call_model")
@patch("app.ai.enrichment.lookup_semantic", return_value=None)
@patch("app.ai.enrichment.build_candidate_shortlist")
async def test_non_empty_ai_match_still_narrows(
    mock_build, mock_lookup, mock_call, mock_log, mock_settings
) -> None:
    """Контраст: непустой ответ модели работает по-прежнему."""
    mock_build.return_value = [_candidate("1")]
    mock_call.return_value = AIEnrichmentAnswer(
        matched_complex_ids=["1"],
        center_district_ids=[],
        poi_findings={},
        explanation="",
        confidence=0.9,
    )
    warnings: list[str] = []

    result = await enrich("хочу со школой", _poi_criteria(), warnings, pool=None)

    assert result.complexes_matched is True
    assert result.matched_complex_ids == ["1"]
    assert AI_NO_MATCH_WARNING not in warnings
