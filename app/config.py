from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Secret for internal refresh endpoint
    INTERNAL_REFRESH_TOKEN: str | None = None

    # Settings for AI
    AI_ENRICHMENT_ENABLED: bool = False
    GEMINI_API_KEY: str | None = None
    AI_MODEL_NAME: str = "gemini-2.5-flash"
    DATABASE_URL: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
