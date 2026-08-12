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
    # OAuth-сессия Claude Code как альтернатива API-ключу: залогинился в
    # терминале (`claude` → /login) — и больше ничего настраивать не нужно.
    # Пустое значение = взять токен из keychain macOS (см.
    # app.ai.client._read_oauth_token). Задавать явно нужно только там, где
    # keychain недоступен (Linux, CI, docker) — тогда сюда кладётся токен,
    # выданный `claude setup-token`.
    CLAUDE_OAUTH_TOKEN: str | None = None
    ANTIGRAVITY_CLI_PATH: str | None = None
    # Путь к бинарю Claude Code CLI (`claude`) для провайдера claude. Пусто =
    # авто-дискавери самим Claude Agent SDK (см. app/ai/client.py, call_claude —
    # транспорт по образцу workflow-ai: SDK сам запускает локальный CLI и берёт
    # авторизацию из логина Claude Code).
    CLAUDE_CLI_PATH: str = ""
    # Потолок числа ходов одного вызова Claude — cost-guard. Для нашего
    # одношагового структурного ответа (tools=[], без тул-лупа) хватает малого
    # значения; держим небольшой запас на служебные ходы SDK.
    CLAUDE_MAX_TURNS: int = 4
    # Единственное поле «какую модель звать» — используется обоими провайдерами
    # (передаётся в `agy --model` для antigravity и в ClaudeAgentOptions.model
    # для claude). Раньше существовало отдельное ANTIGRAVITY_MODEL, из-за чего
    # AI_MODEL_NAME по факту не использовался при провайдере antigravity
    # (AUDIT_REPORT 1.4) — поле убрано, источник истины теперь один.
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

    # Ретраи вызовов ИИ-провайдеров (Claude/Antigravity) — app/ai/client.py.
    # Живой инцидент: до этой правки ретрай был ровно один и без задержки,
    # повтор гарантированно попадал в то же rate-limit-окно (429 делит квоту с
    # локальной OAuth-сессией Claude Code). AI_RETRY_MAX_ATTEMPTS — общее число
    # попыток (включая первую), не число повторов. Ретраятся только транзиентные
    # сбои (таймаут, 429/5xx) — 4xx-ошибки клиента (неверный запрос/ключ) не
    # ретраятся никогда, повтор лишь удвоит задержку без шанса на успех.
    AI_RETRY_MAX_ATTEMPTS: int = 3
    # База экспоненты (секунды) для backoff, когда сервер не прислал
    # Retry-After: задержка attempt-й попытки ~= AI_RETRY_BASE_DELAY_SECONDS *
    # 2^(attempt-1) + джиттер (до +20%, чтобы параллельные запросы не били в
    # квоту синхронной волной).
    AI_RETRY_BASE_DELAY_SECONDS: float = 0.5
    # Потолок суммарной задержки ретраев ОДНОГО вызова — не даёт латентности
    # ИИ-слоя на пользовательский запрос раздуться неограниченно (ни экспонентой,
    # ни большим Retry-After от сервера).
    AI_RETRY_MAX_DELAY_SECONDS: float = 10.0

    # Circuit breaker уровня процесса (app/ai/client.py) — после серии
    # транзиентных отказов подряд ИИ-слой уходит в cooldown и перестаёт делать
    # сетевые вызовы вовсе, вместо того чтобы на каждый новый пользовательский
    # запрос заново упираться в исчерпанную квоту (самоусиливающийся отказ).
    # Полностью выключается флагом (например, для локальной отладки с реальным
    # провайдером, когда единичные 429 ожидаемы и не должны блокировать сервис).
    AI_CIRCUIT_BREAKER_ENABLED: bool = True
    # Число последовательных транзиентных отказов (после исчерпания ретраев),
    # после которого breaker открывается. Успешный вызов сбрасывает счётчик.
    AI_CIRCUIT_BREAKER_THRESHOLD: int = 2
    # Длительность cooldown в секундах: пока breaker открыт, enrich()/
    # resolve_options() деградируют без единого сетевого вызова.
    AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS: float = 60.0

    # Отладочные флаги для проверки правок промпта/кода ИИ-слоя на живом запросе
    # (не для прод-трафика). Семантический кэш (app/ai/memory.py) хранит ответ
    # ИИ бессрочно, без версии по промпту/коду — правка правится, а старый ответ
    # на тот же текст возвращается неизменным, пока embedding попадает в порог
    # схожести. AI_ENRICHMENT_BYPASS_CACHE=true пропускает только ЧТЕНИЕ кэша
    # (lookup_semantic в app/ai/enrichment.py) — запись (persist/store_semantic)
    # продолжает идти как обычно, прод-кэш не теряется.
    AI_ENRICHMENT_BYPASS_CACHE: bool = False
    # Гейт 2 (fully_resolved, Milestone AI-20) возвращает детерминированный
    # результат и ИИ не зовёт вовсе, если все POI/центр-факты уже известны из
    # кэша — это не связано с семантическим кэшем выше и bypass его не отключит.
    # AI_ENRICHMENT_FORCE=true пропускает именно этот гейт, чтобы прогнать через
    # модель даже полностью детерминированный запрос (для отладки).
    AI_ENRICHMENT_FORCE: bool = False

    # Яндекс.Карты и маршрутизация: API-ключи и настройки автозагрузки
    YANDEX_MAPS_API_KEY: str | None = None
    YANDEX_MAPS_ROUTING_API_KEY: str | None = None
    YANDEX_MAPS_AUTO_LOAD: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
