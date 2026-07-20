# 17 — Карта памяти: семантический кэш + структурированные факты (Postgres + pgvector) — Milestone AI-2

> Прочитай `_conventions.md`, `docs/ai-enrichment-architecture.md` (промпт 15)
> и project skills `postgres`/`pgvector-semantic-search` перед началом — они
> задают golden path по типам колонок/индексам, не изобретай свой.

## Контекст

Центральное требование задачи: «все ответы должны сохраняться в карте памяти
и обучать бэк самому отвечать на эти вопросы». Это значит два разных типа
хранения, которые нельзя смешивать в одну таблицу:

1. **Структурированные факты** — конкретные утверждения вида «у ЖК X есть
   школа в радиусе 500м» или «ЖК X считается ‘в центре’». Они привязаны к
   конкретной сущности справочника (`complex`/`district`/`county`) и
   конкретной категории факта, а не к формулировке вопроса — это то, что
   в итоге промоутится (промпт 20) в чистый детерминированный слой без ИИ и
   без БД вообще.
2. **Семантический кэш** — сырые вопросы пользователя (точнее, их
   нераспознанный остаток после `parse()`), которые ещё не свелись к
   структурному факту (например, расплывчатое «рядом с зеленью» без явной
   категории). Для них нужен поиск по смыслу (embedding + ANN), а не точное
   совпадение строки.

## Задача

### 1. Схема БД

Новая директория `app/ai/migrations/` (простые `.sql`-файлы, без тяжёлого
ORM/Alembic — проект и так лёгкий, миграции применяются вручную/при старте).

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE ai_structured_facts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    subject_type TEXT NOT NULL,        -- 'complex' | 'district' | 'county'
    subject_id TEXT NOT NULL,          -- id из соответствующего справочника
    fact_type TEXT NOT NULL,           -- 'is_center' | 'poi_school' | 'poi_kindergarten' | ...
    fact_value JSONB NOT NULL,         -- {"present": true, "distance_m": 340}
    source TEXT NOT NULL DEFAULT 'ai', -- 'ai' | 'geo' | 'curated' | 'promoted'
    confidence REAL NOT NULL DEFAULT 1.0,
    observed_count INT NOT NULL DEFAULT 1,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (subject_type, subject_id, fact_type)
);

CREATE TABLE ai_semantic_cache (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_signature TEXT NOT NULL,      -- нормализованный нераспознанный остаток текста
    embedding halfvec(384) NOT NULL,    -- размерность = выбранная embedding-модель, см. п.3
    raw_question TEXT NOT NULL,
    answer JSONB NOT NULL,
    hit_count INT NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON ai_semantic_cache USING hnsw (embedding halfvec_cosine_ops);
```

Следовать golden path skill `pgvector-semantic-search`: `halfvec`, HNSW,
`cosine` (`<=>`), `SET hnsw.ef_search` на уровне запроса, явный `::halfvec(N)`
каст. Размерность 384 — под п.3 (может отличаться, если выбрана другая
embedding-модель — тогда поменять и здесь, и в коде синхронно, они должны
совпадать всегда).

### 2. Модуль `app/ai/memory.py`

Асинхронный клиент (пул соединений через `psycopg`/`asyncpg` — выбрать один и
следовать project skill `postgres`), функции:

- `async def lookup_structured_fact(subject_type, subject_id, fact_type) ->
  StructuredFact | None`
- `async def store_structured_fact(subject_type, subject_id, fact_type,
  value, source, confidence) -> None` — `UPSERT` (`ON CONFLICT ... DO UPDATE`)
  с инкрементом `observed_count`, обновлением `last_confirmed_at` и (если
  новое значение совпадает со старым) повышением `confidence`; если значение
  **разошлось** с уже сохранённым — не затирать молча: логировать конфликт и
  решать по политике (например, побеждает более свежий более уверенный ответ,
  но старый остаётся виден в логе/истории — не обязательно версионировать в
  v2, но не терять факт конфликта).
- `async def lookup_semantic(query_signature, embedding, threshold=0.15) ->
  CachedAnswer | None` — поиск ближайшего соседа по `<=>`, фильтр по порогу
  дистанции (не по количеству — семантически непохожий ближайший сосед не
  должен возвращаться как «найдено»); при попадании — `touch()`
  (инкремент `hit_count`, обновление `last_used_at`).
- `async def store_semantic(query_signature, embedding, raw_question,
  answer) -> None`.

### 3. Эмбеддинги

Использовать **локальную** embedding-модель (без платного вызова на каждый
запрос — это прямо соответствует цели задачи «меньше вызовов к ИИ»), например
`sentence-transformers` с компактной моделью (уточнить точное имя/размерность
на месте реализации, зафиксировать выбор в
`docs/ai-enrichment-architecture.md` и синхронизировать размерность с
`halfvec(N)` в схеме). Обернуть в `app/ai/embeddings.py`:
`embed(text: str) -> list[float]`, с кэшем модели в памяти процесса (не
загружать веса на каждый вызов).

### 4. Промоушен — только точка расширения здесь, реализация в промпте 20

`ai_structured_facts.source`/`observed_count`/`confidence` — это именно то,
по чему промпт 20 будет отбирать факты для переноса в
`app/reference/*.json`/`app/geo/`. Здесь достаточно, что поля существуют и
корректно накапливаются.

## Требования к результату

- Тесты в `tests/ai/test_memory.py` — по project skill `postgres`: тестовая БД
  (docker-compose сервис `postgres` с расширением `pgvector`, см. skill
  `docker-compose-orchestration` — добавить сервис в `docker-compose.yml`,
  которого пока в проекте нет, создать минимальный) или fixture с
  транзакционным откатом между тестами. **Реальный вызов Anthropic API в
  этих тестах не нужен** — тестируется только слой памяти.
- `lookup_semantic` покрыт тестом на: точное повторение вопроса → hit;
  перефразированный, но близкий по смыслу вопрос → hit; совсем другой вопрос
  → miss (порог не даёт ложных срабатываний) — используй реальную маленькую
  embedding-модель в тестах (детерминированный локальный инференс, сеть не
  нужна), не мокай сами эмбеддинги, иначе тест ничего не проверяет.
- `uv run pytest`, `uv run ruff check` зелёные; `AI_ENRICHMENT_ENABLED=false`
  по умолчанию — модуль `memory.py` не должен требовать поднятой БД для
  прохождения остального тестового набора (00–16), только для своих тестов.

## Обнови CLAUDE.md и `docs/ai-enrichment-architecture.md`

- Схема таблиц, выбранная embedding-модель и её размерность, команда подъёма
  локальной БД для разработки (`docker compose up -d postgres`), путь к
  миграциям и как их накатить.
- Раздел «Стек» CLAUDE.md: добавить PostgreSQL 15+/`pgvector`,
  `sentence-transformers` (или выбранную альтернативу), `psycopg`/`asyncpg`.

## Зависит от

15, 16 (нужны `subject_type`/`subject_id` из справочников и `POICategory` из
гео-слоя).
