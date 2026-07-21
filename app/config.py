from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Secret for internal refresh endpoint
    INTERNAL_REFRESH_TOKEN: str | None = None

    # Settings for AI
    AI_ENRICHMENT_ENABLED: bool = False
    AI_PROVIDER: str = "claude"
    ANTHROPIC_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    # Дешёвая быстрая модель для семантического отбора ЖК по короткому шорт-листу
    # (высокочастотный рантайм-путь). Haiku 4.5 — актуальная замена устаревшей
    # claude-3-haiku-20240307.
    AI_MODEL_NAME: str = "claude-haiku-4-5"
    DATABASE_URL: str | None = None

    # Пороги промоушена ИИ-фактов в детерминированные справочники
    # (см. app/ai/promotion.py). Вынесены в конфиг, чтобы оператор мог ужесточить
    # вентиль без правки кода. Число независимых наблюдений — главная защита от
    # единичной уверенной галлюцинации модели (одна confidence=0.9 сама по себе
    # промоушен не даёт: нужно ≥ AI_PROMOTION_MIN_OBSERVATIONS разных запросов).
    AI_PROMOTION_MIN_OBSERVATIONS: int = 5
    AI_PROMOTION_MIN_CONFIDENCE: float = 0.8

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
