-- Наблюдаемость вызовов ИИ (Milestone AI-11).
--
-- Таблица фиксирует по одной строке на каждый вызов app.ai.enrichment.enrich():
-- решение «когда fallback-гейты можно вернуть» переводится с даты/ощущения на
-- измеримые цифры (отчёт: python -m app.ai.usage_report).
--
-- Ключевая колонка — fully_resolved_deterministically: она вычисляется и
-- логируется ВСЕГДА, даже пока гейт 2 закомментирован и не влияет на реальный
-- ответ. Это и есть измерение «что было бы, если включить гейт»: доля true над
-- окном запросов — критерий возврата гейтов (см. docs/ai-enrichment-architecture.md).

CREATE TABLE ai_call_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Сработал бы старый гейт 1 (в запросе есть poi_requirements/center_requested).
    had_poi_or_center BOOLEAN NOT NULL,
    -- Результат fully_resolved(): запрос можно закрыть детерминированно (гейт 2).
    -- Логируется всегда, даже пока гейт 2 выключен.
    fully_resolved_deterministically BOOLEAN NOT NULL,
    -- Ответ взят из семантического кэша (ИИ не звался).
    cache_hit BOOLEAN NOT NULL DEFAULT false,
    -- Реально ли дёрнули LLM/agy на этом запросе.
    ai_called BOOLEAN NOT NULL DEFAULT false,
    -- Отличался ли итоговый criteria после ИИ от детерминированного слоя
    -- (реальная польза вызова, а не просто факт вызова).
    criteria_changed_by_ai BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX ai_call_log_occurred_at_idx ON ai_call_log (occurred_at);
