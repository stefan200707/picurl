"""Тесты ретраев/backoff и circuit breaker в app/ai/client.py (Milestone: см.
CLAUDE.md, раздел про 429-инцидент общей квоты OAuth-сессии).

Ничего не ходит в сеть и не запускает CLI: провайдер claude ходит через Claude
Agent SDK — здесь подменяется его функция ``query`` (async-генератор фейковых
``ResultMessage``); subprocess ``agy`` подменяется фейковым процессом;
``asyncio.sleep``/``random.uniform`` мокаются — тесты проверяют РЕАЛЬНЫЕ вызовы
сна (backoff действительно ждёт), а не полагаются на настоящие таймауты.

Транзиентный сбой claude моделируем двумя путями, как в реальности: (1) ошибочный
``ResultMessage`` с HTTP-кодом 429/5xx (``api_error_status``) и (2) инфраструктурный
сбой CLI (``CLIConnectionError``, брошенный ``query``). Нетранзиентное — код 4xx и
брак валидации ответа модели.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from claude_agent_sdk import CLIConnectionError, ResultMessage
from pydantic import ValidationError

from app.ai.client import (
    CircuitOpenError,
    ClaudeSDKCallError,
    call_antigravity,
    call_claude,
    reset_circuit_breaker,
)
from app.ai.schema import AIEnrichmentAnswer
from app.config import get_settings

_ANSWER = AIEnrichmentAnswer(
    matched_complex_ids=["1"],
    center_district_ids=[],
    poi_findings={},
    explanation="ok",
    confidence=0.9,
)


def _result_message(
    *,
    structured_output: dict | None = None,
    is_error: bool = False,
    api_error_status: int | None = None,
    result: str | None = None,
) -> ResultMessage:
    """Минимальный настоящий ResultMessage SDK с нужными полями (без похода в CLI)."""
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="s",
        result=result,
        structured_output=structured_output,
        api_error_status=api_error_status,
    )


def _agen(*messages):
    """Свежий async-генератор, отдающий заданные сообщения (как настоящий query)."""

    async def _gen(*_args, **_kwargs):
        for message in messages:
            yield message

    return _gen()


def _agen_raise(exc: Exception):
    """async-генератор, который бросает exc при первой итерации (сбой CLI)."""

    async def _gen(*_args, **_kwargs):
        if False:  # делает функцию async-генератором; тело недостижимо
            yield
        raise exc

    return _gen()


def _ok(answer: AIEnrichmentAnswer = _ANSWER):
    return _agen(_result_message(structured_output=answer.model_dump()))


def _err(status: int):
    return _agen(_result_message(is_error=True, api_error_status=status, result="rate limit"))


@pytest.fixture
def client_settings():
    """Настройки ретраев/circuit breaker — быстрые дефолты для тестов (реальный
    sleep всё равно замокан, но короткие значения делают тест читаемым)."""
    settings = get_settings()
    original = {
        "ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY,
        "AI_MODEL_NAME": settings.AI_MODEL_NAME,
        "CLAUDE_CLI_PATH": settings.CLAUDE_CLI_PATH,
        "CLAUDE_MAX_TURNS": settings.CLAUDE_MAX_TURNS,
        "AI_RETRY_MAX_ATTEMPTS": settings.AI_RETRY_MAX_ATTEMPTS,
        "AI_RETRY_BASE_DELAY_SECONDS": settings.AI_RETRY_BASE_DELAY_SECONDS,
        "AI_RETRY_MAX_DELAY_SECONDS": settings.AI_RETRY_MAX_DELAY_SECONDS,
        "AI_CIRCUIT_BREAKER_ENABLED": settings.AI_CIRCUIT_BREAKER_ENABLED,
        "AI_CIRCUIT_BREAKER_THRESHOLD": settings.AI_CIRCUIT_BREAKER_THRESHOLD,
        "AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS": settings.AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    }
    # ANTHROPIC_API_KEY задан => claude_credentials_available True (call_claude не
    # отвалится на «нет учётных данных» ещё до вызова query).
    settings.ANTHROPIC_API_KEY = "sk-test"
    settings.AI_MODEL_NAME = "claude-sonnet-5"
    settings.CLAUDE_CLI_PATH = ""
    settings.CLAUDE_MAX_TURNS = 4
    settings.AI_RETRY_MAX_ATTEMPTS = 3
    settings.AI_RETRY_BASE_DELAY_SECONDS = 1.0
    settings.AI_RETRY_MAX_DELAY_SECONDS = 100.0
    settings.AI_CIRCUIT_BREAKER_ENABLED = True
    settings.AI_CIRCUIT_BREAKER_THRESHOLD = 2
    settings.AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60.0
    reset_circuit_breaker()
    yield settings
    for key, value in original.items():
        setattr(settings, key, value)
    reset_circuit_breaker()


# --- Разбор ответа SDK -------------------------------------------------------


@pytest.mark.asyncio
@patch("app.ai.client.query")
async def test_call_claude_parses_structured_output(mock_query, client_settings):
    """Нативный structured_output SDK валидируется в типизированный ответ."""
    mock_query.side_effect = [_ok()]

    result = await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    assert result == _ANSWER
    # query вызван именно как query(prompt=..., options=...).
    _, kwargs = mock_query.call_args
    assert "prompt" in kwargs and "options" in kwargs


@pytest.mark.asyncio
@patch("app.ai.client.query")
async def test_call_claude_falls_back_to_text_json(mock_query, client_settings):
    """CLI без output_format: structured_output пуст, JSON приходит в result —
    разбираем текстовый фолбэк (в т.ч. в ```json-ограждении)."""
    text = "```json\n" + json.dumps(_ANSWER.model_dump()) + "\n```"
    mock_query.side_effect = [_agen(_result_message(result=text))]

    result = await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    assert result == _ANSWER


@pytest.mark.asyncio
@patch("app.ai.client.query")
async def test_call_claude_invalid_output_not_retried(mock_query, client_settings):
    """Брак ответа модели (structured_output не по схеме) — ошибка валидации,
    не транзиент: сразу наружу, без повторов."""
    mock_query.side_effect = [_agen(_result_message(structured_output={"bogus": True}))]

    with pytest.raises(ValidationError):
        await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    assert mock_query.call_count == 1


# --- A. Экспоненциальный backoff --------------------------------------------


@pytest.mark.asyncio
@patch("app.ai.client.random.uniform", return_value=0.0)
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.query")
async def test_call_claude_retries_transient_with_growing_backoff(
    mock_query, mock_sleep, _mock_jitter, client_settings
):
    """Транзиентный сбой (429) повторяется с растущей экспонентой; итоговый
    успех возвращает ответ модели без лишних попыток."""
    mock_query.side_effect = [_err(429), _err(429), _ok()]

    result = await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    assert result == _ANSWER
    assert mock_query.call_count == 3
    # backoff действительно ждал (не немедленный повтор, как было до фикса).
    assert mock_sleep.await_count == 2
    delays = [call.args[0] for call in mock_sleep.await_args_list]
    # база=1.0: 1-я попытка -> 1.0*2^0=1.0, 2-я -> 1.0*2^1=2.0 (джиттер занулён).
    assert delays == [1.0, 2.0]


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.query")
async def test_call_claude_retries_cli_infra_failure(mock_query, mock_sleep, client_settings):
    """Инфраструктурный сбой CLI (обрыв связи) — транзиентный, повторяется."""
    mock_query.side_effect = [_agen_raise(CLIConnectionError("dropped")), _ok()]

    result = await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    assert result == _ANSWER
    mock_sleep.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.query")
async def test_call_claude_does_not_retry_4xx(mock_query, mock_sleep, client_settings):
    """4xx (кроме 429) — ошибка клиента, повтор не имеет смысла: сразу наружу."""
    mock_query.side_effect = [_err(400)]

    with pytest.raises(ClaudeSDKCallError):
        await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    assert mock_query.call_count == 1
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.ai.client.random.uniform", return_value=0.0)
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.query")
async def test_call_claude_caps_total_retry_delay(
    mock_query, mock_sleep, _mock_jitter, client_settings
):
    """Потолок суммарной задержки не даёт латентности раздуться: как только
    бюджет исчерпан, повтор прекращается и исходная ошибка поднимается."""
    client_settings.AI_RETRY_MAX_ATTEMPTS = 5
    client_settings.AI_RETRY_BASE_DELAY_SECONDS = 10.0
    client_settings.AI_RETRY_MAX_DELAY_SECONDS = 1.0

    mock_query.side_effect = [_err(429) for _ in range(5)]

    with pytest.raises(ClaudeSDKCallError):
        await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    # 1-я попытка: делей=min(10, 1)=1 -> спит, бюджет исчерпан (1-1=0).
    # 2-я попытка: остаток бюджета <=0 -> сразу поднимаем ошибку, без 3-й попытки.
    assert mock_query.call_count == 2
    mock_sleep.assert_awaited_once_with(1.0)


# --- B. Circuit breaker -------------------------------------------------------


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.query")
async def test_circuit_breaker_opens_after_consecutive_failures_and_blocks_calls(
    mock_query, mock_sleep, client_settings
):
    """После N (=2) последовательных транзиентных отказов breaker открывается,
    и следующий вызов НЕ делает обращения к SDK вовсе."""
    client_settings.AI_RETRY_MAX_ATTEMPTS = 1  # без внутренних ретраев — проще считать отказы
    mock_query.side_effect = [_err(429), _err(429)]

    with pytest.raises(ClaudeSDKCallError):
        await call_claude("system", {"q": "1"}, client_settings, AIEnrichmentAnswer)
    with pytest.raises(ClaudeSDKCallError):
        await call_claude("system", {"q": "2"}, client_settings, AIEnrichmentAnswer)

    assert mock_query.call_count == 2

    with pytest.raises(CircuitOpenError):
        await call_claude("system", {"q": "3"}, client_settings, AIEnrichmentAnswer)

    # Ключевая проверка: обращения к SDK не было — breaker сработал ДО вызова.
    assert mock_query.call_count == 2


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.query")
async def test_circuit_breaker_closes_after_cooldown_elapses(
    mock_query, mock_sleep, client_settings
):
    """По истечении cooldown breaker снова пропускает вызовы к провайдеру."""
    client_settings.AI_RETRY_MAX_ATTEMPTS = 1
    client_settings.AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60.0
    mock_query.side_effect = [_err(429), _err(429), _ok()]

    with pytest.raises(ClaudeSDKCallError):
        await call_claude("system", {"q": "1"}, client_settings, AIEnrichmentAnswer)
    with pytest.raises(ClaudeSDKCallError):
        await call_claude("system", {"q": "2"}, client_settings, AIEnrichmentAnswer)
    with pytest.raises(CircuitOpenError):
        await call_claude("system", {"q": "3"}, client_settings, AIEnrichmentAnswer)

    # Симулируем истечение cooldown — белый ящик: состояние breaker модульное.
    import app.ai.client as client_module

    client_module._circuit_opened_until = 0.0

    result = await call_claude("system", {"q": "4"}, client_settings, AIEnrichmentAnswer)
    assert result == _ANSWER


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.query")
async def test_circuit_breaker_resets_on_success(mock_query, mock_sleep, client_settings):
    """Успешный вызов сбрасывает счётчик отказов — одиночные сбои не копятся
    бесконечно и не открывают breaker раньше срока."""
    client_settings.AI_RETRY_MAX_ATTEMPTS = 1
    mock_query.side_effect = [_err(429), _ok(), _err(429)]

    with pytest.raises(ClaudeSDKCallError):
        await call_claude("system", {"q": "1"}, client_settings, AIEnrichmentAnswer)
    await call_claude("system", {"q": "2"}, client_settings, AIEnrichmentAnswer)  # успех — сброс
    with pytest.raises(ClaudeSDKCallError):
        await call_claude("system", {"q": "3"}, client_settings, AIEnrichmentAnswer)

    # Ни один из вызовов не должен был схлопнуться в CircuitOpenError —
    # порог (2 отказа ПОДРЯД) ни разу не достигнут благодаря сбросу.
    assert mock_query.call_count == 3


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.query")
async def test_circuit_breaker_can_be_disabled(mock_query, mock_sleep, client_settings):
    """AI_CIRCUIT_BREAKER_ENABLED=False — предохранитель полностью выключен."""
    client_settings.AI_CIRCUIT_BREAKER_ENABLED = False
    client_settings.AI_RETRY_MAX_ATTEMPTS = 1
    mock_query.side_effect = [_err(429) for _ in range(5)]

    for _ in range(5):
        with pytest.raises(ClaudeSDKCallError):
            await call_claude("system", {"q": "x"}, client_settings, AIEnrichmentAnswer)

    # Ни разу не поднялся CircuitOpenError — каждый вызов реально дошёл до SDK.
    assert mock_query.call_count == 5


def test_reset_circuit_breaker_is_idempotent():
    reset_circuit_breaker()
    reset_circuit_breaker()


# --- Распространение на call_antigravity -------------------------------------


class _FakeProcess:
    def __init__(self, returncode: int, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.fixture
def antigravity_settings():
    settings = get_settings()
    original = {
        "ANTIGRAVITY_CLI_PATH": settings.ANTIGRAVITY_CLI_PATH,
        "AI_RETRY_MAX_ATTEMPTS": settings.AI_RETRY_MAX_ATTEMPTS,
        "AI_RETRY_BASE_DELAY_SECONDS": settings.AI_RETRY_BASE_DELAY_SECONDS,
        "AI_RETRY_MAX_DELAY_SECONDS": settings.AI_RETRY_MAX_DELAY_SECONDS,
        "AI_CIRCUIT_BREAKER_ENABLED": settings.AI_CIRCUIT_BREAKER_ENABLED,
        "AI_CIRCUIT_BREAKER_THRESHOLD": settings.AI_CIRCUIT_BREAKER_THRESHOLD,
    }
    settings.ANTIGRAVITY_CLI_PATH = "agy"
    settings.AI_RETRY_MAX_ATTEMPTS = 3
    settings.AI_RETRY_BASE_DELAY_SECONDS = 1.0
    settings.AI_RETRY_MAX_DELAY_SECONDS = 100.0
    settings.AI_CIRCUIT_BREAKER_ENABLED = True
    settings.AI_CIRCUIT_BREAKER_THRESHOLD = 2
    reset_circuit_breaker()
    yield settings
    for key, value in original.items():
        setattr(settings, key, value)
    reset_circuit_breaker()


@pytest.mark.asyncio
@patch("app.ai.client.random.uniform", return_value=0.0)
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_call_antigravity_retries_transient_process_failure(
    mock_spawn, mock_sleep, _mock_jitter, antigravity_settings
):
    """Ненулевой код возврата agy CLI — транзиентный сбой (напр. временная
    недоступность/rate limit провайдера под капотом CLI), повторяем с backoff."""
    ok_stdout = json.dumps(_ANSWER.model_dump()).encode()
    mock_spawn.side_effect = [
        _FakeProcess(returncode=1, stderr=b"rate limited"),
        _FakeProcess(returncode=0, stdout=ok_stdout),
    ]

    result = await call_antigravity(
        "system", {"q": "test"}, antigravity_settings, AIEnrichmentAnswer
    )

    assert result == _ANSWER
    assert mock_spawn.await_count == 2
    mock_sleep.assert_awaited_once_with(1.0)


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_call_antigravity_does_not_retry_invalid_json(
    mock_spawn, mock_sleep, antigravity_settings
):
    """Успешный exit-код, но мусор вместо JSON — ошибка валидации ответа модели,
    а не сеть/CLI: повтор с тем же промптом почти наверняка даст тот же брак,
    поэтому НЕ ретраится."""
    mock_spawn.return_value = _FakeProcess(returncode=0, stdout=b"not json at all")

    with pytest.raises(ValidationError):
        await call_antigravity("system", {"q": "test"}, antigravity_settings, AIEnrichmentAnswer)

    assert mock_spawn.await_count == 1
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client.asyncio.create_subprocess_exec", new_callable=AsyncMock)
async def test_call_antigravity_circuit_breaker_blocks_without_spawning(
    mock_spawn, mock_sleep, antigravity_settings
):
    """Circuit breaker общий для обоих провайдеров: после порога отказов
    call_antigravity тоже не порождает subprocess вовсе."""
    antigravity_settings.AI_RETRY_MAX_ATTEMPTS = 1
    mock_spawn.side_effect = [
        _FakeProcess(returncode=1, stderr=b"boom"),
        _FakeProcess(returncode=1, stderr=b"boom"),
    ]

    with pytest.raises(RuntimeError):
        await call_antigravity("system", {"q": "1"}, antigravity_settings, AIEnrichmentAnswer)
    with pytest.raises(RuntimeError):
        await call_antigravity("system", {"q": "2"}, antigravity_settings, AIEnrichmentAnswer)

    assert mock_spawn.await_count == 2

    with pytest.raises(CircuitOpenError):
        await call_antigravity("system", {"q": "3"}, antigravity_settings, AIEnrichmentAnswer)

    assert mock_spawn.await_count == 2
