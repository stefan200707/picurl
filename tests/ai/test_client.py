"""Тесты ретраев/backoff и circuit breaker в app/ai/client.py (Milestone: см.
CLAUDE.md, раздел про 429-инцидент общей квоты OAuth-сессии).

Ничего не ходит в сеть: Anthropic-клиент подменяется фейком, subprocess
``agy`` подменяется фейковым процессом, ``asyncio.sleep``/``random.uniform``
мокаются — тесты проверяют РЕАЛЬНЫЕ вызовы сна (backoff действительно ждёт),
а не полагаются на настоящие таймауты.
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from anthropic import APIStatusError, APITimeoutError
from pydantic import ValidationError

from app.ai.client import (
    CircuitOpenError,
    call_antigravity,
    call_claude,
    reset_circuit_breaker,
)
from app.ai.schema import AIEnrichmentAnswer
from app.config import get_settings


def _status_error(status_code: int, retry_after: str | None = None) -> APIStatusError:
    """Сконструировать настоящий anthropic.APIStatusError с нужным статусом и,
    опционально, заголовком Retry-After — без похода в сеть (httpx.Response
    строится вручную)."""
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx.Response(status_code, request=request, headers=headers)
    return APIStatusError("boom", response=response, body={"error": {"type": "x"}})


def _timeout_error() -> APITimeoutError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return APITimeoutError(request=request)


_ANSWER = AIEnrichmentAnswer(
    matched_complex_ids=["1"],
    center_district_ids=[],
    poi_findings={},
    explanation="ok",
    confidence=0.9,
)


class _FakeMessage:
    def __init__(self, tool_input: dict):
        self.type = "tool_use"
        self.name = "provide_enrichment_answer"
        self.input = tool_input


class _FakeResponse:
    def __init__(self, tool_input: dict):
        self.content = [_FakeMessage(tool_input)]


@pytest.fixture
def client_settings():
    """Настройки ретраев/circuit breaker — быстрые дефолты для тестов (реальный
    sleep всё равно замокан, но короткие значения делают тест читаемым)."""
    settings = get_settings()
    original = {
        "ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY,
        "AI_RETRY_MAX_ATTEMPTS": settings.AI_RETRY_MAX_ATTEMPTS,
        "AI_RETRY_BASE_DELAY_SECONDS": settings.AI_RETRY_BASE_DELAY_SECONDS,
        "AI_RETRY_MAX_DELAY_SECONDS": settings.AI_RETRY_MAX_DELAY_SECONDS,
        "AI_CIRCUIT_BREAKER_ENABLED": settings.AI_CIRCUIT_BREAKER_ENABLED,
        "AI_CIRCUIT_BREAKER_THRESHOLD": settings.AI_CIRCUIT_BREAKER_THRESHOLD,
        "AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS": settings.AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    }
    settings.ANTHROPIC_API_KEY = "sk-test"
    settings.AI_RETRY_MAX_ATTEMPTS = 3
    settings.AI_RETRY_BASE_DELAY_SECONDS = 1.0
    settings.AI_RETRY_MAX_DELAY_SECONDS = 100.0
    settings.AI_CIRCUIT_BREAKER_ENABLED = True
    settings.AI_CIRCUIT_BREAKER_THRESHOLD = 2
    settings.AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60.0
    yield settings
    for key, value in original.items():
        setattr(settings, key, value)


# --- A. Экспоненциальный backoff с retry-after -------------------------------


@pytest.mark.asyncio
@patch("app.ai.client.random.uniform", return_value=0.0)
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client._get_claude_client")
async def test_call_claude_retries_transient_with_growing_backoff(
    mock_get_client, mock_sleep, _mock_jitter, client_settings
):
    """Транзиентный сбой (429) повторяется с растущей экспонентой; итоговый
    успех возвращает ответ модели без лишних попыток."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        side_effect=[
            _status_error(429),
            _status_error(429),
            _FakeResponse(_ANSWER.model_dump()),
        ]
    )
    mock_get_client.return_value = fake_client

    result = await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    assert result == _ANSWER
    assert fake_client.messages.create.await_count == 3
    # backoff действительно ждал (не немедленный повтор, как было до фикса).
    assert mock_sleep.await_count == 2
    delays = [call.args[0] for call in mock_sleep.await_args_list]
    # база=1.0: 1-я попытка -> 1.0*2^0=1.0, 2-я -> 1.0*2^1=2.0 (джиттер занулён).
    assert delays == [1.0, 2.0]


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client._get_claude_client")
async def test_call_claude_respects_retry_after_header(
    mock_get_client, mock_sleep, client_settings
):
    """Retry-After от сервера уважается вместо собственной экспоненты."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        side_effect=[
            _status_error(429, retry_after="5"),
            _FakeResponse(_ANSWER.model_dump()),
        ]
    )
    mock_get_client.return_value = fake_client

    await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    mock_sleep.assert_awaited_once_with(5.0)


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client._get_claude_client")
async def test_call_claude_does_not_retry_4xx(mock_get_client, mock_sleep, client_settings):
    """4xx (кроме 429) — ошибка клиента, повтор не имеет смысла: сразу наружу."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(side_effect=_status_error(400))
    mock_get_client.return_value = fake_client

    with pytest.raises(APIStatusError):
        await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    assert fake_client.messages.create.await_count == 1
    mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client._get_claude_client")
async def test_call_claude_retries_timeout(mock_get_client, mock_sleep, client_settings):
    """Таймаут — тоже транзиентная ошибка, повторяется."""
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        side_effect=[_timeout_error(), _FakeResponse(_ANSWER.model_dump())]
    )
    mock_get_client.return_value = fake_client

    result = await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    assert result == _ANSWER
    mock_sleep.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.ai.client.random.uniform", return_value=0.0)
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client._get_claude_client")
async def test_call_claude_caps_total_retry_delay(
    mock_get_client, mock_sleep, _mock_jitter, client_settings
):
    """Потолок суммарной задержки не даёт латентности раздуться: как только
    бюджет исчерпан, повтор прекращается и исходная ошибка поднимается."""
    client_settings.AI_RETRY_MAX_ATTEMPTS = 5
    client_settings.AI_RETRY_BASE_DELAY_SECONDS = 10.0
    client_settings.AI_RETRY_MAX_DELAY_SECONDS = 1.0

    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(side_effect=_status_error(429))
    mock_get_client.return_value = fake_client

    with pytest.raises(APIStatusError):
        await call_claude("system", {"q": "test"}, client_settings, AIEnrichmentAnswer)

    # 1-я попытка: делей=min(10, 1)=1 -> спит, бюджет исчерпан (1-1=0).
    # 2-я попытка: остаток бюджета <=0 -> сразу поднимаем ошибку, без 3-й попытки.
    assert fake_client.messages.create.await_count == 2
    mock_sleep.assert_awaited_once_with(1.0)


# --- B. Circuit breaker -------------------------------------------------------


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client._get_claude_client")
async def test_circuit_breaker_opens_after_consecutive_failures_and_blocks_calls(
    mock_get_client, mock_sleep, client_settings
):
    """После N (=2) последовательных транзиентных отказов breaker открывается,
    и следующий вызов НЕ делает сетевого обращения вовсе."""
    client_settings.AI_RETRY_MAX_ATTEMPTS = 1  # без внутренних ретраев — проще считать отказы
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(side_effect=_status_error(429))
    mock_get_client.return_value = fake_client

    with pytest.raises(APIStatusError):
        await call_claude("system", {"q": "1"}, client_settings, AIEnrichmentAnswer)
    with pytest.raises(APIStatusError):
        await call_claude("system", {"q": "2"}, client_settings, AIEnrichmentAnswer)

    assert fake_client.messages.create.await_count == 2

    with pytest.raises(CircuitOpenError):
        await call_claude("system", {"q": "3"}, client_settings, AIEnrichmentAnswer)

    # Ключевая проверка: сетевого вызова не было — breaker сработал ДО обращения.
    assert fake_client.messages.create.await_count == 2


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client._get_claude_client")
async def test_circuit_breaker_closes_after_cooldown_elapses(
    mock_get_client, mock_sleep, client_settings
):
    """По истечении cooldown breaker снова пропускает вызовы к провайдеру."""
    client_settings.AI_RETRY_MAX_ATTEMPTS = 1
    client_settings.AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS = 60.0
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(side_effect=_status_error(429))
    mock_get_client.return_value = fake_client

    with pytest.raises(APIStatusError):
        await call_claude("system", {"q": "1"}, client_settings, AIEnrichmentAnswer)
    with pytest.raises(APIStatusError):
        await call_claude("system", {"q": "2"}, client_settings, AIEnrichmentAnswer)
    with pytest.raises(CircuitOpenError):
        await call_claude("system", {"q": "3"}, client_settings, AIEnrichmentAnswer)

    # Симулируем истечение cooldown — белый ящик: состояние breaker модульное.
    import app.ai.client as client_module

    client_module._circuit_opened_until = 0.0

    fake_client.messages.create.side_effect = [_FakeResponse(_ANSWER.model_dump())]
    result = await call_claude("system", {"q": "4"}, client_settings, AIEnrichmentAnswer)
    assert result == _ANSWER


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client._get_claude_client")
async def test_circuit_breaker_resets_on_success(mock_get_client, mock_sleep, client_settings):
    """Успешный вызов сбрасывает счётчик отказов — одиночные сбои не копятся
    бесконечно и не открывают breaker раньше срока."""
    client_settings.AI_RETRY_MAX_ATTEMPTS = 1
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(
        side_effect=[
            _status_error(429),
            _FakeResponse(_ANSWER.model_dump()),
            _status_error(429),
        ]
    )
    mock_get_client.return_value = fake_client

    with pytest.raises(APIStatusError):
        await call_claude("system", {"q": "1"}, client_settings, AIEnrichmentAnswer)
    await call_claude("system", {"q": "2"}, client_settings, AIEnrichmentAnswer)  # успех — сброс
    with pytest.raises(APIStatusError):
        await call_claude("system", {"q": "3"}, client_settings, AIEnrichmentAnswer)

    # Ни один из вызовов не должен был схлопнуться в CircuitOpenError —
    # порог (2 отказа ПОДРЯД) ни разу не достигнут благодаря сбросу.
    assert fake_client.messages.create.await_count == 3


@pytest.mark.asyncio
@patch("app.ai.client.asyncio.sleep", new_callable=AsyncMock)
@patch("app.ai.client._get_claude_client")
async def test_circuit_breaker_can_be_disabled(mock_get_client, mock_sleep, client_settings):
    """AI_CIRCUIT_BREAKER_ENABLED=False — предохранитель полностью выключен."""
    client_settings.AI_CIRCUIT_BREAKER_ENABLED = False
    client_settings.AI_RETRY_MAX_ATTEMPTS = 1
    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(side_effect=_status_error(429))
    mock_get_client.return_value = fake_client

    for _ in range(5):
        with pytest.raises(APIStatusError):
            await call_claude("system", {"q": "x"}, client_settings, AIEnrichmentAnswer)

    # Ни разу не поднялся CircuitOpenError — каждый вызов реально дошёл до сети.
    assert fake_client.messages.create.await_count == 5


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
    yield settings
    for key, value in original.items():
        setattr(settings, key, value)


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
