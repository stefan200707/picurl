import asyncio
import functools
import json
import logging
import platform
import random
import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Literal, NamedTuple

import httpx
from anthropic import APIStatusError, APITimeoutError, AsyncAnthropic
from pydantic import BaseModel

from app.ai.schema import AIEnrichmentAnswer, OptionResolutionAnswer
from app.config import get_settings

logger = logging.getLogger(__name__)

# Заголовок, разрешающий OAuth-токену ходить в Messages API. Без него токен
# сессии отвергается (для API-ключа заголовок не нужен и не отправляется).
_OAUTH_BETA = "oauth-2025-04-20"
# Имя записи в keychain macOS, куда Claude Code кладёт учётные данные логина.
_KEYCHAIN_SERVICE = "Claude Code-credentials"
# Токен в keychain обновляется самим CLI, поэтому перечитываем его, а не берём
# один раз на старте процесса. Короткий TTL — компромисс: не дёргаем keychain
# (блокирующий subprocess) на каждый запрос, но подхватываем refresh за минуту.
_TOKEN_TTL_SECONDS = 60.0

_token_cache: tuple[float, str | None] = (0.0, None)


class ClaudeCredentials(NamedTuple):
    """Чем аутентифицируемся в Anthropic API.

    ``kind="api_key"`` → заголовок ``x-api-key``; ``kind="oauth"`` →
    ``Authorization: Bearer`` + бета-заголовок. Одновременно отправлять оба
    нельзя — API отвергает такой запрос, поэтому это именно выбор одного из.
    """

    kind: Literal["api_key", "oauth"]
    secret: str


def _read_keychain_token() -> str | None:
    """OAuth-токен активной сессии Claude Code из keychain macOS.

    Возвращает ``None``, если пользователь не залогинен, keychain недоступен
    или платформа не macOS — вызывающий код трактует это как «ИИ не настроен»
    и деградирует на детерминированный пайплайн.
    """
    if platform.system() != "Darwin":
        return None
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if raw.returncode != 0:
            return None
        data = json.loads(raw.stdout)
        return (data.get("claudeAiOauth") or {}).get("accessToken") or None
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        logger.warning("Не удалось прочитать OAuth-токен Claude из keychain", exc_info=True)
        return None


def _read_oauth_token() -> str | None:
    """Токен из настроек (приоритет) либо из keychain, с кэшем на TTL."""
    global _token_cache

    settings = get_settings()
    if settings.CLAUDE_OAUTH_TOKEN:
        return settings.CLAUDE_OAUTH_TOKEN

    cached_at, cached_token = _token_cache
    now = time.monotonic()
    if cached_token is not None and now - cached_at < _TOKEN_TTL_SECONDS:
        return cached_token

    token = _read_keychain_token()
    _token_cache = (now, token)
    return token


def resolve_claude_credentials(settings) -> ClaudeCredentials | None:
    """Как аутентифицироваться в Anthropic: ключ, OAuth-сессия или никак.

    API-ключ имеет приоритет (явная конфигурация сильнее неявной сессии).
    ``None`` означает «учётных данных нет» — не ошибка, а сигнал выключить
    ИИ-слой; базовый пайплайн от этого не страдает.
    """
    if settings.ANTHROPIC_API_KEY:
        return ClaudeCredentials("api_key", settings.ANTHROPIC_API_KEY)
    token = _read_oauth_token()
    if token:
        return ClaudeCredentials("oauth", token)
    return None


@functools.cache
def _get_claude_client(kind: str, secret: str) -> AsyncAnthropic:
    """Переиспользуемый Anthropic-клиент (кэш по учётным данным) — не создаём
    httpx-пул на каждый запрос."""
    if kind == "oauth":
        return AsyncAnthropic(
            auth_token=secret,
            default_headers={"anthropic-beta": _OAUTH_BETA},
            timeout=httpx.Timeout(15.0),
        )
    return AsyncAnthropic(api_key=secret, timeout=httpx.Timeout(15.0))


def _is_transient(exc: Exception) -> bool:
    """Стоит ли повторять запрос: только таймаут и 429/5xx (не 4xx-ошибки клиента)."""
    if isinstance(exc, APITimeoutError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


def _is_transient_antigravity(exc: Exception) -> bool:
    """Транзиентный сбой agy CLI: ненулевой код возврата (см. RuntimeError,
    которым мы оборачиваем такой выход ниже) или ошибка запуска процесса
    (``OSError`` — бинарник временно недоступен). Ошибка валидации JSON-ответа
    модели (``pydantic.ValidationError``) НЕ транзиентна: тот же промпт почти
    наверняка даст тот же брак, повтор только тратит бюджет задержки впустую.
    """
    return isinstance(exc, RuntimeError | OSError)


def _extract_retry_after(exc: Exception) -> float | None:
    """Уважить заголовок ``Retry-After`` ответа API, если он есть — сервер лучше
    нас знает, когда квота освободится, чем наша собственная экспонента.
    Поддерживается только числовой формат (секунды) — единственный, который
    реально отдаёт Anthropic API; HTTP-date формат не встречался на практике,
    его парсинг не реализован (некорректное значение просто игнорируется, и
    вызывающий код падает обратно на экспоненциальный backoff)."""
    response = getattr(exc, "response", None)
    if response is None:
        return None
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


class CircuitOpenError(RuntimeError):
    """ИИ-слой временно отключён: circuit breaker в состоянии cooldown после
    серии транзиентных отказов подряд (см. :func:`_record_circuit_failure`).
    Поднимается ДО сетевого вызова — в этом весь смысл предохранителя: не
    увеличивать нагрузку на провайдера ровно тогда, когда его квота уже
    исчерпана (самоусиливающийся отказ, живой инцидент — см. CLAUDE.md)."""


# Состояние circuit breaker — уровень процесса (модульные переменные), общее
# для обоих провайдеров (Claude/Antigravity) и для всех обычных вызовов, и
# резолвинга опций: один и тот же rate-limit делится всеми путями enrich().
# asyncio.Lock не привязывается к event loop при создании (Python 3.10+),
# поэтому модульный синглтон безопасен даже с учётом того, что pytest-asyncio
# создаёт новый event loop на каждый тест.
_circuit_lock = asyncio.Lock()
_consecutive_failures = 0
_circuit_opened_until = 0.0  # time.monotonic(); 0.0 = breaker закрыт


def reset_circuit_breaker() -> None:
    """Сбросить состояние circuit breaker. Нужен тестам для изоляции — без
    сброса между тестами открытый одним тестом breaker ломал бы соседние
    (состояние модульное, а не per-request)."""
    global _consecutive_failures, _circuit_opened_until
    _consecutive_failures = 0
    _circuit_opened_until = 0.0


async def _check_circuit_or_raise(settings) -> None:
    if not settings.AI_CIRCUIT_BREAKER_ENABLED:
        return
    async with _circuit_lock:
        opened_until = _circuit_opened_until
    if opened_until and time.monotonic() < opened_until:
        remaining = opened_until - time.monotonic()
        raise CircuitOpenError(
            f"серия транзиентных отказов подряд, повтор возможен через {remaining:.0f} с"
        )


async def _record_circuit_failure(settings) -> None:
    """Учесть отказ (после исчерпания ретраев) в счётчике breaker'а. Считаются
    только ТРАНЗИЕНТНЫЕ отказы — вызывающий код (:func:`_execute_with_retry`)
    гарантирует, что сюда не попадают 4xx/ошибки валидации: их повтор не имеет
    смысла, но и не сигнализирует об исчерпании квоты."""
    global _consecutive_failures, _circuit_opened_until
    if not settings.AI_CIRCUIT_BREAKER_ENABLED:
        return
    async with _circuit_lock:
        _consecutive_failures += 1
        if _consecutive_failures >= settings.AI_CIRCUIT_BREAKER_THRESHOLD:
            _circuit_opened_until = time.monotonic() + settings.AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS
            logger.error(
                f"AI circuit breaker OPEN: {_consecutive_failures} транзиентных отказов подряд, "
                f"cooldown {settings.AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS}s"
            )


async def _record_circuit_success() -> None:
    global _consecutive_failures, _circuit_opened_until
    async with _circuit_lock:
        _consecutive_failures = 0
        _circuit_opened_until = 0.0


async def _execute_with_retry[T](
    attempt_fn: Callable[[], Awaitable[T]],
    *,
    is_transient: Callable[[Exception], bool],
    settings,
    extract_retry_after: Callable[[Exception], float | None] = lambda exc: None,
) -> T:
    """Общий экспоненциальный backoff (с джиттером, уважением Retry-After) и
    circuit breaker для вызовов ИИ-провайдеров.

    Перед первой попыткой проверяет breaker (:func:`_check_circuit_or_raise`) —
    если открыт, сетевой вызов не делается вовсе. Повторяются только
    транзиентные сбои (``is_transient``); нетранзиентные (4xx, ошибки
    валидации) поднимаются немедленно, без ретрая и без влияния на breaker.
    Суммарная задержка всех ретраев ОДНОГО вызова ограничена
    ``AI_RETRY_MAX_DELAY_SECONDS`` — иначе латентность ИИ-слоя на один
    пользовательский запрос могла бы расти неограниченно (было: 15с таймаут ×
    до 4 вызовов = до 60с).
    """
    await _check_circuit_or_raise(settings)

    max_attempts = max(1, settings.AI_RETRY_MAX_ATTEMPTS)
    base_delay = settings.AI_RETRY_BASE_DELAY_SECONDS
    max_total_delay = settings.AI_RETRY_MAX_DELAY_SECONDS
    total_slept = 0.0

    for attempt in range(1, max_attempts + 1):
        try:
            result = await attempt_fn()
        except Exception as exc:
            if not is_transient(exc):
                # 4xx/ошибка валидации — не в счётчик breaker'а, повтор бессмыслен.
                logger.warning(f"Non-transient AI provider error, not retrying: {exc}")
                raise
            if attempt >= max_attempts:
                logger.error(
                    f"AI provider call failed after {attempt}/{max_attempts} attempts: {exc}"
                )
                await _record_circuit_failure(settings)
                raise
            delay = extract_retry_after(exc)
            if delay is None:
                raw = base_delay * (2 ** (attempt - 1))
                delay = raw + random.uniform(0.0, raw * 0.2)
            remaining_budget = max_total_delay - total_slept
            if remaining_budget <= 0:
                logger.error(
                    f"AI provider retry budget ({max_total_delay}s) exhausted on "
                    f"attempt {attempt}/{max_attempts}: {exc}"
                )
                await _record_circuit_failure(settings)
                raise
            delay = min(max(delay, 0.0), remaining_budget)
            logger.warning(
                f"Transient AI provider error (attempt {attempt}/{max_attempts}), "
                f"retrying in {delay:.2f}s: {exc}"
            )
            await asyncio.sleep(delay)
            total_slept += delay
        else:
            await _record_circuit_success()
            return result

    raise AssertionError("unreachable: retry loop must return or raise")


async def call_typed[T: BaseModel](
    system_prompt: str, user_payload: dict, answer_model: type[T]
) -> T:
    """Единая точка вызова модели, параметризованная схемой ответа.

    Позволяет переиспользовать один транспорт (Anthropic tool-use / Antigravity
    CLI) для разных задач: обогащение ЖК (:class:`AIEnrichmentAnswer`) и
    резолвинг фраз под опции (:class:`OptionResolutionAnswer`).
    """
    settings = get_settings()
    if not settings.AI_ENRICHMENT_ENABLED:
        raise ValueError("AI enrichment is disabled")

    provider = settings.AI_PROVIDER.lower()

    if provider == "antigravity":
        return await call_antigravity(system_prompt, user_payload, settings, answer_model)
    elif provider == "claude":
        return await call_claude(system_prompt, user_payload, settings, answer_model)
    else:
        raise ValueError(f"Unknown AI_PROVIDER: {provider}")


async def call_model(system_prompt: str, user_payload: dict) -> AIEnrichmentAnswer:
    return await call_typed(system_prompt, user_payload, AIEnrichmentAnswer)


async def call_option_resolver(system_prompt: str, user_payload: dict) -> OptionResolutionAnswer:
    """Вызов модели для резолвинга нераспознанных фраз под опции/группы опций."""
    return await call_typed(system_prompt, user_payload, OptionResolutionAnswer)


async def call_antigravity[T: BaseModel](
    system_prompt: str,
    user_payload: dict,
    settings,
    answer_model: type[T] = AIEnrichmentAnswer,
) -> T:
    cli_path = settings.ANTIGRAVITY_CLI_PATH or "agy"
    # Единый источник «какую модель звать» (см. app/config.Settings.AI_MODEL_NAME).
    model = settings.AI_MODEL_NAME

    user_message = json.dumps(user_payload, ensure_ascii=False)

    schema = answer_model.model_json_schema()
    full_prompt = (
        f"{system_prompt}\n\n"
        "IMPORTANT: You must respond ONLY with valid JSON matching this schema: "
        f"{json.dumps(schema)}\n\n"
        f"Input: {user_message}"
    )

    # Безопасность вызова agy (Milestone AI-11): agy — агентный CLI, а в
    # full_prompt попадает полный текст пользователя (user_query) без
    # санитизации — это поверхность prompt injection → действия агента.
    # Поэтому НИКОГДА не передаём `--dangerously-skip-permissions` и запускаем
    # строго в неинтерактивном режиме `--print` (только вывод текста, без
    # разрешения агенту выполнять действия). Не добавляйте сюда флагов,
    # снимающих ограничения на действия.
    cmd = [cli_path, "--print", full_prompt]
    if model:
        cmd.extend(["--model", model])

    async def _invoke() -> T:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            raise RuntimeError(f"agy CLI failed (exit {process.returncode}): {stderr.decode()}")

        full_response = stdout.decode().strip()

        if full_response.startswith("```json"):
            full_response = full_response[7:]
        if full_response.startswith("```"):
            full_response = full_response[3:]
        if full_response.endswith("```"):
            full_response = full_response[:-3]

        full_response = full_response.strip()
        return answer_model.model_validate_json(full_response)

    # Ретрай + circuit breaker (Milestone: 429-инцидент общей квоты) —
    # распространены и на antigravity: раньше этот путь не ретраился вовсе.
    return await _execute_with_retry(
        _invoke,
        is_transient=_is_transient_antigravity,
        settings=settings,
    )


async def call_claude[T: BaseModel](
    system_prompt: str,
    user_payload: dict,
    settings,
    answer_model: type[T] = AIEnrichmentAnswer,
) -> T:
    credentials = resolve_claude_credentials(settings)
    if credentials is None:
        raise ValueError(
            "Нет учётных данных Claude: задайте ANTHROPIC_API_KEY либо "
            "залогиньтесь в терминале командой `claude` (пункт /login)"
        )

    client = _get_claude_client(*credentials)

    user_message = json.dumps(user_payload, ensure_ascii=False)

    tool = {
        "name": "provide_enrichment_answer",
        "description": "Provide the AI enrichment answer.",
        "input_schema": answer_model.model_json_schema(),
    }

    async def _create() -> T:
        response = await client.messages.create(
            model=settings.AI_MODEL_NAME,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "provide_enrichment_answer"},
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "provide_enrichment_answer":
                return answer_model.model_validate(block.input)
        # Не транзиентный сбой (ответ пришёл, но без ожидаемого tool-use блока) —
        # ретраить бессмысленно, `_is_transient` его и не сочтёт временным.
        raise ValueError("Model did not return tool use block")

    # Ретрай (экспоненциальный backoff + Retry-After) и circuit breaker —
    # повторяем только временные сбои (таймаут, 429/5xx); 4xx (неверный
    # запрос/ключ) не ретраим — это лишь удвоит ошибку и задержку.
    return await _execute_with_retry(
        _create,
        is_transient=_is_transient,
        extract_retry_after=_extract_retry_after,
        settings=settings,
    )
