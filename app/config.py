from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Secret for internal refresh endpoint
    INTERNAL_REFRESH_TOKEN: str | None = None

    # Settings for AI
    AI_ENRICHMENT_ENABLED: bool = True
    # Провайдер ИИ по умолчанию — Antigravity (CLI `agy`, дефолтная модель
    # gemini-3.5-flash). Для Claude выставить AI_PROVIDER=claude + ANTHROPIC_API_KEY.
    AI_PROVIDER: str = "antigravity"
    ANTHROPIC_API_KEY: str | None = None
    ANTIGRAVITY_CLI_PATH: str | None = None
    # Единственное поле «какую модель звать» — используется обоими провайдерами
    # (передаётся в `agy --model` для antigravity и в Anthropic API для claude).
    # Раньше существовало отдельное ANTIGRAVITY_MODEL, из-за чего AI_MODEL_NAME по
    # факту не использовался при провайдере antigravity (AUDIT_REPORT 1.4) —
    # поле убрано, источник истины теперь один.
    AI_MODEL_NAME: str = "gemini-3.5-flash"
    # Единый источник строки подключения к БД ИИ (AUDIT_REPORT 2.8): раньше
    # app/ai/memory.py читал DATABASE_URL напрямую через os.getenv в обход
    # pydantic-settings, а это поле было мёртвым. Дефолт сохранён прежним.
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/picurl_ai"

    # Пороги промоушена ИИ-фактов в детерминированные справочники
    # (см. app/ai/promotion.py). Вынесены в конфиг, чтобы оператор мог ужесточить
    # вентиль без правки кода. Число независимых наблюдений — главная защита от
    # единичной уверенной галлюцинации модели (одна confidence=0.9 сама по себе
    # промоушен не даёт: нужно ≥ AI_PROMOTION_MIN_OBSERVATIONS разных запросов).
    AI_PROMOTION_MIN_OBSERVATIONS: int = 5
    AI_PROMOTION_MIN_CONFIDENCE: float = 0.8

    # Доля наблюдений фразы-алиаса, которая должна указывать на один и тот же slug,
    # чтобы алиас можно было промоутить (см. app/ai/promotion.py, ветка
    # option_alias). 1.0 = требуется единогласие: любое расхождение фразы между
    # разными slug трактуется как конфликт (промпт 17) и НИКОГДА не промоутится
    # автоматически — фраза уходит в отчёт «неоднозначные, требуют ручного
    # решения». Оператор может ослабить до «подавляющего большинства», понизив
    # порог, но делать это осознанно.
    AI_ALIAS_PROMOTION_MIN_CONSISTENCY: float = 1.0

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
