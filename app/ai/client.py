import json
import logging

import httpx
from anthropic import APIStatusError, APITimeoutError, AsyncAnthropic

from app.ai.schema import AIEnrichmentAnswer
from app.config import get_settings

logger = logging.getLogger(__name__)


async def call_model(system_prompt: str, user_payload: dict) -> AIEnrichmentAnswer:
    settings = get_settings()
    if not settings.AI_ENRICHMENT_ENABLED or not settings.ANTHROPIC_API_KEY:
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
