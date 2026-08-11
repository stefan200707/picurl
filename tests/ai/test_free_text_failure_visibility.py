"""Правка Г5: провал free-text-ветки ИИ обязан быть виден снаружи.

Дефект: `resolve_free_text_criteria` глушила оба пути отказа молча — исключение
вызова уходило в `logger.error`, а cooldown circuit breaker'а возвращал
`FreeTextOutcome(called=False)`, неотличимый от «модель не звали». У
`FreeTextOutcome` не было признака провала вовсе, поэтому до `EnrichmentResult`
он доехать не мог: ответ API с `ai_used=false, ai_failed=false` означал
одновременно «ИИ не звали», «позвали и он ничего не поменял» и «позвали и
упало». Три состояния под одной парой флагов — нарушение инварианта 1 на уровне
телеметрии.

Границы: провал = модель НЕ ответила (исключение или breaker). «Ответила, но
ничего не заполнила» и «значения отбиты валидацией» — успешный вызов без пользы
(`failed=False`), их различает лог, а не флаг. Отсутствие кредов/выключенный ИИ —
`disabled`, НЕ провал (инвариант 9).
"""

from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai.client import CircuitOpenError
from app.ai.enrichment import enrich, resolve_free_text_criteria
from app.ai.schema import FreeTextCriteriaAnswer
from app.api.endpoints import get_http_client
from app.config import get_settings
from app.main import app
from app.parsing.schema import Criteria, Sort

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


# --- Уровень FreeTextOutcome: провал получает признак и причину ---


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_exception_marks_outcome_failed(mock_extractor, mock_settings):
    """Исключение вызова: попытка была (called) и провалилась (failed)."""
    mock_extractor.side_effect = RuntimeError("провайдер лёг")
    criteria = Criteria()
    warnings = [_RESIDUAL.format("этаж от 7")]

    outcome = await resolve_free_text_criteria(criteria, "двушка этаж от 7", warnings, None)

    assert outcome.called is True
    assert outcome.failed is True
    assert outcome.failure_reason == "exception"
    assert outcome.changed is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_circuit_breaker_marks_outcome_failed(mock_extractor, mock_settings):
    """Cooldown breaker'а: до модели не дошли (called=False), но это ОТКАЗ ИИ-слоя,
    а не «не звали» — иначе провал неотличим от гейта."""
    mock_extractor.side_effect = CircuitOpenError("cooldown 42s")
    criteria = Criteria()
    warnings = [_RESIDUAL.format("этаж от 7")]

    outcome = await resolve_free_text_criteria(criteria, "двушка этаж от 7", warnings, None)

    assert outcome.called is False
    assert outcome.failed is True
    assert outcome.failure_reason == "breaker"


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_failure_leaves_fragments_and_adds_warning(mock_extractor, mock_settings):
    """Инвариант 1: фрагменты остаются в warnings + строка о том, ПОЧЕМУ они там."""
    mock_extractor.side_effect = RuntimeError("провайдер лёг")
    criteria = Criteria()
    warnings = [_RESIDUAL.format("этаж от 7"), _RESIDUAL.format("в 10 минутах ходьбы")]

    await resolve_free_text_criteria(criteria, "двушка этаж от 7", warnings, None)

    assert _RESIDUAL.format("этаж от 7") in warnings
    assert _RESIDUAL.format("в 10 минутах ходьбы") in warnings
    assert any("ИИ" in w and "не" in w for w in warnings[2:]), warnings


# --- Границы: что провалом НЕ является ---


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_empty_answer_is_not_failure(mock_extractor, mock_settings):
    """Модель ответила и ничего не заполнила — успешный вызов без пользы."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(consumed_fragments=[])
    criteria = Criteria()
    warnings = [_RESIDUAL.format("этаж от 7")]

    outcome = await resolve_free_text_criteria(criteria, "двушка этаж от 7", warnings, None)

    assert outcome.called is True
    assert outcome.failed is False
    assert outcome.failure_reason is None


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_rejected_by_validation_is_not_failure(mock_extractor, mock_settings):
    """Значение отбито validate_assignment — тоже успешный вызов, не провал."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        price_min=-5, consumed_fragments=["этаж от 7"]
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("этаж от 7")]

    outcome = await resolve_free_text_criteria(criteria, "двушка этаж от 7", warnings, None)

    assert outcome.failed is False
    assert outcome.changed is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_success_is_not_failure(mock_extractor, mock_settings):
    """Поведение при успехе не меняется: failed=False, causa нет."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        sort=Sort.PRICE_ASC, consumed_fragments=["подешевле сначала"], explanation="по цене"
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("подешевле сначала")]

    outcome = await resolve_free_text_criteria(
        criteria, "квартира подешевле сначала", warnings, None
    )

    assert outcome.changed is True
    assert outcome.failed is False
    assert warnings == []


@pytest.mark.asyncio
@patch("app.ai.enrichment.claude_credentials_available", return_value=False)
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_missing_credentials_is_not_failure(mock_extractor, _mock_creds, mock_settings):
    """Инвариант 9: нет кредов = ИИ выключен, это НЕ ошибка. Модель не зовём."""
    criteria = Criteria()
    warnings = [_RESIDUAL.format("этаж от 7")]

    outcome = await resolve_free_text_criteria(criteria, "двушка этаж от 7", warnings, None)

    assert outcome.called is False
    assert outcome.failed is False
    assert outcome.failure_reason is None
    mock_extractor.assert_not_called()
    assert warnings == [_RESIDUAL.format("этаж от 7")]  # лишней строки не добавили


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_disabled_ai_is_not_failure(mock_extractor, mock_settings):
    """Выключенный ИИ-слой — тоже не провал (та же ветка, другой признак)."""
    mock_settings.AI_ENRICHMENT_ENABLED = False

    outcome = await resolve_free_text_criteria(
        Criteria(), "двушка этаж от 7", [_RESIDUAL.format("этаж от 7")], None
    )

    assert outcome.failed is False
    assert outcome.called is False
    mock_extractor.assert_not_called()


# --- Уровень enrich(): провал доезжает до EnrichmentResult и ai_call_log ---


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call")
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_enrich_propagates_failure_to_meta(mock_extractor, mock_log, mock_settings):
    """Гейт 1 вернул бы noop() (ai_failed=False по конструктору) — провал
    free-text всё равно обязан пережить этот путь."""
    mock_extractor.side_effect = RuntimeError("провайдер лёг")
    criteria = Criteria()  # ни poi/center/landmark/station — гейт 1 сработает
    warnings = [_RESIDUAL.format("этаж от 7")]

    result = await enrich("двушка этаж от 7", criteria, warnings)

    assert result.meta.ai_failed is True
    assert result.meta.ai_used is False  # упал — ни на что не повлиял


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call")
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_enrich_logs_failed_row(mock_extractor, mock_log, mock_settings):
    """Падение оставляет запись в ai_call_log с признаком провала."""
    mock_extractor.side_effect = RuntimeError("провайдер лёг")

    await enrich("двушка этаж от 7", Criteria(), [_RESIDUAL.format("этаж от 7")])

    mock_log.assert_awaited_once()
    assert mock_log.await_args.kwargs["ai_failed"] is True
    assert mock_log.await_args.kwargs["ai_called"] is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.log_ai_call")
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_enrich_logs_breaker_as_failed(mock_extractor, mock_log, mock_settings):
    """Breaker: ai_called=False (до модели не дошли), но ai_failed=True —
    иначе в логе отказ сливается с «не звали»."""
    mock_extractor.side_effect = CircuitOpenError("cooldown 42s")

    await enrich("двушка этаж от 7", Criteria(), [_RESIDUAL.format("этаж от 7")])

    assert mock_log.await_args.kwargs["ai_failed"] is True
    assert mock_log.await_args.kwargs["ai_called"] is False


# --- Критерий готовности: видно по ответу API ---


@pytest.fixture
def client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 47})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_http_client] = lambda: http_client
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@patch("app.ai.enrichment.call_free_text_extractor")
def test_api_response_shows_failed_attempt(mock_extractor, client, mock_settings):
    """Искусственно уронив free-text-вызов, по ответу API видно: попытка была и
    не удалась. До правки здесь было ai_used=false, ai_failed=false — ответ,
    неотличимый от «ИИ не звали»."""
    mock_extractor.side_effect = RuntimeError("провайдер лёг")

    response = client.post(
        "/build-url", json={"text": "Двушку, этаж от 7, чтобы окна выходили на закат"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ai_failed"] is True, body
    assert body["ai_used"] is False
    assert any("окна выходили на закат" in w for w in body["warnings"])
