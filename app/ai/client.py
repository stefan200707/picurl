import functools
import json
import logging
import platform
import subprocess
import time
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
    import asyncio

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

    try:
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
    except Exception as e:
        logger.error(f"Antigravity Agent error: {e}", exc_info=True)
        raise e


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

    async def _create():
        return await client.messages.create(
            model=settings.AI_MODEL_NAME,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "provide_enrichment_answer"},
        )

    try:
        response = await _create()
    except Exception as e:
        # Повторяем только временные сбои (таймаут, 429/5xx); 4xx (неверный
        # запрос/ключ) не ретраим — это лишь удвоит ошибку и задержку.
        if not _is_transient(e):
            raise
        logger.warning(f"Anthropic API transient error, retrying: {e}")
        response = await _create()

    for block in response.content:
        if block.type == "tool_use" and block.name == "provide_enrichment_answer":
            return answer_model.model_validate(block.input)

    raise ValueError("Model did not return tool use block")
