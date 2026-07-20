import json
import logging

from app.ai.schema import AIEnrichmentAnswer
from app.config import get_settings
from google.antigravity import Agent, LocalAgentConfig

logger = logging.getLogger(__name__)


async def call_model(system_prompt: str, user_payload: dict) -> AIEnrichmentAnswer:
    settings = get_settings()
    if not settings.AI_ENRICHMENT_ENABLED:
        raise ValueError("AI enrichment is disabled")

    # If GEMINI_API_KEY is missing, Agent will try to use the environment variable
    # We still pass it if provided in config.
    config = LocalAgentConfig(
        system_instructions=system_prompt,
        response_schema=AIEnrichmentAnswer,
        api_key=settings.GEMINI_API_KEY if hasattr(settings, "GEMINI_API_KEY") else None,
    )

    user_message = json.dumps(user_payload, ensure_ascii=False)

    try:
        async with Agent(config) as agent:
            response = await agent.chat(user_message)
            text_chunks = []
            async for token in response:
                text_chunks.append(token)
            
            full_response = "".join(text_chunks)
            # The agent is constrained by response_schema, so it returns valid JSON.
            return AIEnrichmentAnswer.model_validate_json(full_response)
    except Exception as e:
        logger.error(f"Antigravity Agent error: {e}", exc_info=True)
        raise e
