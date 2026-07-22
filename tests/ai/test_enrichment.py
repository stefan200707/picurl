from unittest.mock import AsyncMock, patch

import pytest

from app.ai.enrichment import (
    enrich,
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
from app.parsing.schema import Criteria, POIRequirement


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

    assert result.ai_used is True
    assert result.success is False
    assert "не удалось обработать ИИ-обогащение (ошибка сервиса)" in warnings


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
