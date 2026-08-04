"""O1: ветка резолвинга опций обязана сообщать исход наверх.

``resolve_options`` мутирует ``criteria``/``warnings`` по ссылке и возвращала
``None`` — вызывающий код не знал ни что модель звали, ни что она что-то
применила. Живой прогон («с видом на парк, город»): детерминированный слой
находит только ``vidNaPark``, «город» остаётся огрызком, ИИ подбирает
``vidNaGorod`` и снимает его warning — а ответ API при этом сообщает
``ai_used=false``. Флаг означает «ИИ реально повлиял», то есть он врал.

Приём тот же, что уже применён к ведру C: возвращаемый ``OptionsOutcome``,
который ``enrich`` вливает в ``_log`` наравне с ``FreeTextOutcome``.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.ai.client import CircuitOpenError
from app.ai.enrichment import _UNRECOGNIZED_SUFFIX, enrich, resolve_options
from app.ai.schema import OptionMatch, OptionResolutionAnswer
from app.config import get_settings
from app.parsing.schema import Criteria

RESOLVED_SLUG = "vidNaGorod"


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


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_option_resolver")
async def test_outcome_reports_applied_slug(mock_resolver, mock_settings) -> None:
    mock_resolver.return_value = OptionResolutionAnswer(
        matches=[OptionMatch(phrase="город", slug=RESOLVED_SLUG, confidence=0.9)]
    )
    criteria = Criteria()
    warnings = [f"«город{_UNRECOGNIZED_SUFFIX}"]

    outcome = await resolve_options(criteria, ["город"], warnings, None)

    assert outcome.called is True
    assert outcome.changed is True
    assert outcome.failed is False
    assert RESOLVED_SLUG in criteria.options
    assert warnings == []


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_option_resolver")
async def test_outcome_reports_useless_call(mock_resolver, mock_settings) -> None:
    """Модель ответила и ничего не подобрала: звали — да, повлияла — нет."""
    mock_resolver.return_value = OptionResolutionAnswer(matches=[])
    warnings = [f"«город{_UNRECOGNIZED_SUFFIX}"]

    outcome = await resolve_options(Criteria(), ["город"], warnings, None)

    assert outcome.called is True
    assert outcome.changed is False
    assert outcome.failed is False
    assert warnings == [f"«город{_UNRECOGNIZED_SUFFIX}"]


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_option_resolver", side_effect=RuntimeError("boom"))
async def test_outcome_reports_exception(mock_resolver, mock_settings) -> None:
    outcome = await resolve_options(Criteria(), ["город"], [], None)

    assert outcome.failed is True
    assert outcome.failure_reason == "exception"


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_option_resolver", side_effect=CircuitOpenError("cooldown"))
async def test_outcome_reports_breaker(mock_resolver, mock_settings) -> None:
    """Предохранитель в cooldown: до провайдера не дошли, но это отказ ИИ-слоя."""
    outcome = await resolve_options(Criteria(), ["город"], [], None)

    assert outcome.called is False
    assert outcome.failed is True
    assert outcome.failure_reason == "breaker"


@pytest.mark.asyncio
async def test_outcome_when_nothing_to_resolve() -> None:
    outcome = await resolve_options(Criteria(), [], [], None)

    assert outcome.called is False
    assert outcome.changed is False
    assert outcome.failed is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call", new_callable=AsyncMock)
@patch("app.ai.enrichment.call_option_resolver")
async def test_enrich_reports_ai_used_when_only_options_branch_worked(
    mock_resolver, mock_log, mock_settings
) -> None:
    """Ключевой сценарий из живого прогона.

    Гейт 1 схлопывает запрос в noop() (ни POI, ни центра, ни ориентиров), но
    ветка опций до гейта уже применила slug. ``ai_used`` обязан это отразить,
    иначе ответ утверждает, что ИИ не участвовал.
    """
    mock_resolver.return_value = OptionResolutionAnswer(
        matches=[OptionMatch(phrase="город", slug=RESOLVED_SLUG, confidence=0.9)]
    )
    criteria = Criteria()
    warnings = [f"«город{_UNRECOGNIZED_SUFFIX}"]

    result = await enrich("с видом на город", criteria, warnings, option_candidates=["город"])

    assert RESOLVED_SLUG in criteria.options
    assert result.ai_used is True
    assert mock_log.await_args.kwargs["criteria_changed_by_ai"] is True
