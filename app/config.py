from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Secret for internal refresh endpoint
    INTERNAL_REFRESH_TOKEN: str | None = None

    # Settings for AI
    AI_ENRICHMENT_ENABLED: bool = False
    ANTHROPIC_API_KEY: str | None = None
    AI_MODEL_NAME: str = "claude-3-haiku-20240307"
    DATABASE_URL: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
