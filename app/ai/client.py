import asyncio
import json
import logging
import platform
import random
import subprocess
import time
from collections.abc import Awaitable, Callable
from contextlib import aclosing

from claude_agent_sdk import (
    ClaudeAgentOptions,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    query,
)
from pydantic import BaseModel

from app.ai.schema import AIEnrichmentAnswer, FreeTextCriteriaAnswer, OptionResolutionAnswer
from app.config import get_settings

logger = logging.getLogger(__name__)

# Имя записи в keychain macOS, куда Claude Code кладёт учётные данные логина.
_KEYCHAIN_SERVICE = "Claude Code-credentials"
# Токен в keychain обновляется самим CLI, поэтому перечитываем его, а не берём
# один раз на старте процесса. Короткий TTL — компромисс: не дёргаем keychain
# (блокирующий subprocess) на каждый запрос, но подхватываем refresh за минуту.
# Внимание: этот токен здесь НЕ передаётся в модель — он нужен лишь как дешёвый
# признак «пользователь залогинен в Claude Code» для гейта в enrich(). Сам вызов
# модели идёт через Claude Agent SDK → локальный CLI, который берёт авторизацию
# из того же логина сам (см. call_claude).
_TOKEN_TTL_SECONDS = 60.0

_token_cache: tuple[float, str | None] = (0.0, None)

# Подстроки в тексте ошибки результата, помечающие сбой как временный (ретраим),
# а не фатальный. Используются как фолбэк, когда CLI не отдал числовой
# api_error_status.
_TRANSIENT_MARKERS = (
    "rate limit",
    "rate_limit",
    "overloaded",
    "timed out",
    "timeout",
    "connection",
    "temporarily unavailable",
    "error_during_execution",
)


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


def claude_credentials_available(settings) -> bool:
    """Настроен ли доступ к Claude — дешёвый признак для гейта в enrich().

    ``True``, если задан ``ANTHROPIC_API_KEY`` (своя квота) ИЛИ есть OAuth-логин
    Claude Code (переменная окружения либо keychain macOS). ``False`` — не
    ошибка, а сигнал выключить ИИ-слой: базовый детерминированный пайплайн
    самодостаточен. Сам вызов модели авторизуется не этим — CLI/SDK берёт логин
    сам; здесь мы лишь не дёргаем модель, когда заведомо нечем.
    """
    if settings.ANTHROPIC_API_KEY:
        return True
    return _read_oauth_token() is not None


class CircuitOpenError(RuntimeError):
    """ИИ-слой временно отключён: circuit breaker в состоянии cooldown после
    серии транзиентных отказов подряд (см. :func:`_record_circuit_failure`).
    Поднимается ДО сетевого вызова — в этом весь смысл предохранителя: не
    увеличивать нагрузку на провайдера ровно тогда, когда его квота уже
    исчерпана (самоусиливающийся отказ, живой инцидент — см. CLAUDE.md)."""


class ClaudeSDKCallError(RuntimeError):
    """Вызов Claude через Agent SDK вернул ошибочный результат.

    ``status`` — HTTP-код провалившегося вызова API (``ResultMessage.
    api_error_status``, напр. 429/500/529), если CLI его сообщил; иначе
    ``None``. ``detail`` — текст для логов/классификации по маркерам. По этим
    полям :func:`_is_transient_claude_sdk` решает, ретраить ли."""

    def __init__(self, detail: str, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


class ClaudeStreamError(RuntimeError):
    """Поток SDK завершился без ``ResultMessage`` — временный инфраструктурный
    сбой CLI (обрыв стрима), ретраится."""


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


def _is_transient_claude_sdk(exc: Exception) -> bool:
    """Стоит ли повторять вызов Claude через Agent SDK.

    Транзиентны инфраструктурные сбои локального CLI (обрыв связи/процесса,
    битый JSON стрима, оборванный стрим) и ошибочный результат API с кодом
    429/5xx либо текстом-маркером временного отказа. НЕ транзиентны: отсутствие
    CLI (проблема установки — ретрай не поможет) и ошибки валидации ответа
    модели (тот же промпт даст тот же брак)."""
    if isinstance(exc, CLINotFoundError):
        return False
    if isinstance(exc, CLIConnectionError | ProcessError | CLIJSONDecodeError | ClaudeStreamError):
        return True
    if isinstance(exc, ClaudeSDKCallError):
        if exc.status is not None:
            return exc.status == 429 or exc.status >= 500
        detail = exc.detail.lower()
        return any(marker in detail for marker in _TRANSIENT_MARKERS)
    return False


def _is_transient_antigravity(exc: Exception) -> bool:
    """Транзиентный сбой agy CLI: ненулевой код возврата (см. RuntimeError,
    которым мы оборачиваем такой выход ниже) или ошибка запуска процесса
    (``OSError`` — бинарник временно недоступен). Ошибка валидации JSON-ответа
    модели (``pydantic.ValidationError``) НЕ транзиентна: тот же промпт почти
    наверняка даст тот же брак, повтор только тратит бюджет задержки впустую.
    """
    return isinstance(exc, RuntimeError | OSError)


async def call_typed[T: BaseModel](
    system_prompt: str, user_payload: dict, answer_model: type[T]
) -> T:
    """Единая точка вызова модели, параметризованная схемой ответа.

    Позволяет переиспользовать один транспорт (Claude Agent SDK / Antigravity
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


async def call_free_text_extractor(
    system_prompt: str, user_payload: dict
) -> FreeTextCriteriaAnswer:
    """Вызов модели для извлечения недостающих скалярных фильтров из свободного текста."""
    return await call_typed(system_prompt, user_payload, FreeTextCriteriaAnswer)


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


def _claude_sdk_env(settings) -> dict[str, str]:
    """Переменные окружения для дочернего CLI-процесса Claude.

    По умолчанию — пусто: на macOS SDK/CLI берёт логин Claude Code из keychain
    сам. Явный ``ANTHROPIC_API_KEY`` (своя квота, устраняет 429 общей сессии)
    прокидывается в env; ``CLAUDE_OAUTH_TOKEN`` — для сред без keychain
    (Linux/CI), под именем, которое ждёт CLI.
    """
    if settings.ANTHROPIC_API_KEY:
        return {"ANTHROPIC_API_KEY": settings.ANTHROPIC_API_KEY}
    if settings.CLAUDE_OAUTH_TOKEN:
        return {"CLAUDE_CODE_OAUTH_TOKEN": settings.CLAUDE_OAUTH_TOKEN}
    return {}


async def _run_claude_query(prompt: str, options: ClaudeAgentOptions) -> ResultMessage:
    """Один headless-прогон Claude через Agent SDK → локальный CLI.

    Возвращает финальный ``ResultMessage``. Инфраструктурные сбои CLI
    (``CLIConnectionError``/``ProcessError``/``CLIJSONDecodeError``/
    ``CLINotFoundError``) поднимаются как есть — их классифицирует
    :func:`_is_transient_claude_sdk`. Оборвавшийся без результата стрим —
    :class:`ClaudeStreamError` (транзиент).

    ``aclosing`` обязателен: мы выходим по первому ``ResultMessage`` через
    ``return``, не досматривая стрим. Без явного закрытия недоеденный
    async-генератор SDK добивается сборщиком мусора уже на живом event loop —
    отсюда ``RuntimeError: aclose(): asynchronous generator is already running``
    и риск утечки дочернего процесса CLI. ``aclosing`` закрывает его детерминированно
    в момент выхода, когда генератор приостановлен."""
    async with aclosing(query(prompt=prompt, options=options)) as stream:
        async for message in stream:
            if isinstance(message, ResultMessage):
                return message
    raise ClaudeStreamError("поток Claude SDK завершился без ResultMessage")


def _parse_claude_result[T: BaseModel](rm: ResultMessage, answer_model: type[T]) -> T:
    """Разобрать ``ResultMessage`` в типизированный ответ.

    Приоритет — нативный ``structured_output`` (валиден по переданной схеме);
    фолбэк — распарсить JSON из текстового ``result`` (на случай CLI без
    поддержки ``output_format``). Ошибочный результат API поднимается как
    :class:`ClaudeSDKCallError` (с HTTP-кодом, если CLI его сообщил)."""
    if rm.is_error:
        detail = rm.result or ("; ".join(rm.errors or [])) or rm.subtype or "unknown error"
        raise ClaudeSDKCallError(detail, status=rm.api_error_status)

    if rm.structured_output is not None:
        return answer_model.model_validate(rm.structured_output)

    text = (rm.result or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return answer_model.model_validate_json(text.strip())


async def call_claude[T: BaseModel](
    system_prompt: str,
    user_payload: dict,
    settings,
    answer_model: type[T] = AIEnrichmentAnswer,
) -> T:
    """Вызов Claude через Claude Agent SDK поверх локального Claude Code CLI.

    Транспорт по образцу соседнего проекта workflow-ai: SDK сам запускает
    установленный CLI (``claude``) и берёт авторизацию из логина Claude Code —
    без ручного OAuth-токена в заголовках. Структурный ответ модели приходит
    нативно (``output_format`` json-schema → ``structured_output``). Один ход,
    без тул-лупа (``tools=[]``).

    Оговорка про квоту: локальный логин делит лимит с самим Claude Code — под
    нагрузкой возможен 429; для своей квоты задайте ``ANTHROPIC_API_KEY``.
    Транзиентные сбои гасит общий :func:`_execute_with_retry` (backoff +
    circuit breaker).
    """
    if not claude_credentials_available(settings):
        raise ValueError(
            "Нет учётных данных Claude: задайте ANTHROPIC_API_KEY либо "
            "залогиньтесь в терминале командой `claude` (пункт /login)"
        )

    prompt = json.dumps(user_payload, ensure_ascii=False)
    options = ClaudeAgentOptions(
        model=settings.AI_MODEL_NAME or None,
        cli_path=settings.CLAUDE_CLI_PATH or None,
        max_turns=settings.CLAUDE_MAX_TURNS,
        # Никогда не подгружаем пользовательские/проектные настройки и CLAUDE.md
        # хоста — модель видит только свой системный промпт и вход запроса.
        setting_sources=[],
        system_prompt=system_prompt,
        # tools= — жёсткое ограничение: без него у модели остаются доступны
        # Read/Bash/итд. Пустой список => одношаговый структурный ответ.
        tools=[],
        allowed_tools=[],
        output_format={"type": "json_schema", "schema": answer_model.model_json_schema()},
        env=_claude_sdk_env(settings),
    )

    async def _invoke() -> T:
        result_msg = await _run_claude_query(prompt, options)
        return _parse_claude_result(result_msg, answer_model)

    # Ретрай (экспоненциальный backoff) и circuit breaker — повторяем только
    # временные сбои (обрыв CLI, 429/5xx); фатальное (нет CLI, брак валидации)
    # не ретраим — это лишь удвоит ошибку и задержку.
    return await _execute_with_retry(
        _invoke,
        is_transient=_is_transient_claude_sdk,
        settings=settings,
    )
