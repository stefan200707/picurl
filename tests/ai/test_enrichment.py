from unittest.mock import patch

import pytest

from app.ai.enrichment import enrich, sanitize_against_shortlist
from app.ai.schema import AIEnrichmentAnswer, ComplexCandidate
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
