"""Экстрактор свободного текста → Criteria (ведро C, ослабление гейтов).

Новый ИИ-путь достаёт недостающие СКАЛЯРНЫЕ фильтры из непонятого парсером
текста. Здесь зафиксированы инварианты: детерминированный слой выигрывает
(заполняем только пустые поля), невалидное значение отбрасывается, ведро B/шум
(нет реального остатка) не жжёт вызов, снятие warning'ов по consumed_fragments,
и честный ai_used даже когда гейт 1 вернул бы noop().
"""

from unittest.mock import patch

import pytest

from app.ai.enrichment import enrich, resolve_free_text_criteria
from app.ai.schema import FreeTextCriteriaAnswer
from app.config import get_settings
from app.parsing.schema import Criteria, Rooms, Sort

_RESIDUAL = "«{}»: не удалось распознать, не попало в ссылку"


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
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_fills_empty_scalar_and_removes_warning(mock_extractor, mock_settings):
    """Пустое поле заполняется, warning по consumed_fragment снимается."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        sort=Sort.PRICE_ASC,
        consumed_fragments=["по возрастанию стоимости"],
        explanation="сортировка по цене",
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("по возрастанию стоимости")]

    outcome = await resolve_free_text_criteria(
        criteria, "квартира по возрастанию стоимости", warnings, None
    )

    assert outcome.changed is True
    assert criteria.sort == Sort.PRICE_ASC
    assert warnings == []  # фраза снята


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_does_not_override_deterministic(mock_extractor, mock_settings):
    """Детерминированный слой выигрывает: занятое поле НЕ перетирается."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(price_max=99, consumed_fragments=["хрень"])
    criteria = Criteria(price_max=15_000_000)
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    assert criteria.price_max == 15_000_000  # не тронуто
    assert outcome.changed is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_invalid_value_rejected(mock_extractor, mock_settings):
    """Значение вне границ Criteria (price<0) отбрасывается validate_assignment."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(price_min=-5, consumed_fragments=["хрень"])
    criteria = Criteria()
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    assert criteria.price_min is None  # брак не применён
    assert outcome.changed is False
    assert warnings == [_RESIDUAL.format("хрень")]  # warning остался — ничего не разобрано


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_rooms_filled_only_if_empty(mock_extractor, mock_settings):
    """rooms заполняются только при пустом детерминированном списке."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        rooms=[Rooms.ONE, Rooms.TWO], consumed_fragments=["хрень"]
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    assert criteria.rooms == [Rooms.ONE, Rooms.TWO]
    assert outcome.changed is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_bool_flag_set(mock_extractor, mock_settings):
    """Булев флаг включается только True поверх дефолтного False."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        only_available=True, consumed_fragments=["хрень"]
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    assert criteria.only_available is True
    assert outcome.changed is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_no_residual_skips_call(mock_extractor, mock_settings):
    """Нет реального остатка (только «не поддерживается pik.ru») → модель не зовём."""
    criteria = Criteria()
    warnings = [
        "«вторичка»: вторичное жильё не поддерживается pik.ru (только новостройки) — пропущено"
    ]

    outcome = await resolve_free_text_criteria(criteria, "вторичка", warnings, None)

    mock_extractor.assert_not_called()
    assert outcome.called is False
    assert outcome.changed is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_disabled_skips_call(mock_extractor, mock_settings):
    """ИИ выключен → остаток остаётся в warnings, модель не дёргаем."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    criteria = Criteria()
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    mock_extractor.assert_not_called()
    assert outcome.called is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_time_to_metro_safety_net(mock_extractor, mock_settings):
    """ИИ-страховка: время до метро, промахнутое детерминикой, заполняет ИИ."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        time_on_foot=12, consumed_fragments=["до метро рукой подать"]
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("до метро рукой подать")]

    outcome = await resolve_free_text_criteria(criteria, "до метро рукой подать", warnings, None)

    assert criteria.time_on_foot == 12
    assert outcome.changed is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_enrich_sets_ai_used_even_when_gate1_noop(mock_extractor, mock_settings):
    """Ключевой кейс ослабления гейтов: запрос без структурных сигналов (гейт 1
    вернул бы noop()), но экстрактор заполнил поле → ai_used честно True."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        sort=Sort.PRICE_DESC,
        consumed_fragments=["сначала элитные"],
        explanation="дорогие вперёд",
    )
    criteria = Criteria()  # ни poi/center/landmark/station — гейт 1 сработал бы
    warnings = [_RESIDUAL.format("сначала элитные")]

    result = await enrich("квартира сначала элитные", criteria, warnings)

    assert criteria.sort == Sort.PRICE_DESC  # criteria обогащён до гейта
    assert result.meta.ai_used is True  # ИИ реально повлиял
    assert result.meta.explanation == "дорогие вперёд"
