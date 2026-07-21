import json
import logging

import httpx
from anthropic import APIStatusError, APITimeoutError, AsyncAnthropic

from app.ai.schema import AIEnrichmentAnswer
from app.config import get_settings

logger = logging.getLogger(__name__)


async def call_model(system_prompt: str, user_payload: dict) -> AIEnrichmentAnswer:
    settings = get_settings()
    if not settings.AI_ENRICHMENT_ENABLED:
        raise ValueError("AI enrichment is disabled")

    provider = settings.AI_PROVIDER.lower()

    if provider == "antigravity":
        return await call_antigravity(system_prompt, user_payload, settings)
    elif provider == "claude":
        return await call_claude(system_prompt, user_payload, settings)
    else:
        raise ValueError(f"Unknown AI_PROVIDER: {provider}")


async def call_antigravity(system_prompt: str, user_payload: dict, settings) -> AIEnrichmentAnswer:
    import asyncio

    cli_path = settings.ANTIGRAVITY_CLI_PATH or "agy"
    model = settings.ANTIGRAVITY_MODEL

    user_message = json.dumps(user_payload, ensure_ascii=False)

    schema = AIEnrichmentAnswer.model_json_schema()
    full_prompt = (
        f"{system_prompt}\n\n"
        "IMPORTANT: You must respond ONLY with valid JSON matching this schema: "
        f"{json.dumps(schema)}\n\n"
        f"Input: {user_message}"
    )

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
        return AIEnrichmentAnswer.model_validate_json(full_response)
    except Exception as e:
        logger.error(f"Antigravity Agent error: {e}", exc_info=True)
        raise e


async def call_claude(system_prompt: str, user_payload: dict, settings) -> AIEnrichmentAnswer:
    if not settings.ANTHROPIC_API_KEY:
        raise ValueError("AI enrichment is disabled or API key is missing")

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=httpx.Timeout(15.0))

    user_message = json.dumps(user_payload, ensure_ascii=False)

    tool = {
        "name": "provide_enrichment_answer",
        "description": "Provide the AI enrichment answer.",
        "input_schema": AIEnrichmentAnswer.model_json_schema(),
    }

    try:
        response = await client.messages.create(
            model=settings.AI_MODEL_NAME,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            tools=[tool],
            tool_choice={"type": "tool", "name": "provide_enrichment_answer"},
        )
    except (APIStatusError, APITimeoutError) as e:
        logger.warning(f"Anthropic API transient error, retrying: {e}")
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
            return AIEnrichmentAnswer.model_validate(block.input)

    raise ValueError("Model did not return tool use block")
