# CLAUDE.md

Проект: picurl (GitHub: stefan200707/picurl).

## Цель проекта

pik-url-builder
Сервис принимает свободный текст на русском языке о желаемой квартире ("хочу двушку у метро, до 15 млн, с отделкой") и возвращает рабочую ссылку на pik.ru с уже применёнными фильтрами и сортировкой — чтобы не тыкать вручную по чекбоксам на сайте.

---

## Декомпозиция и промпты для сборки

В `prompts/` лежит готовая декомпозиция задачи — набор пронумерованных `.md`
промптов для пошаговой сборки сервиса (по одному на агента/шаг). Порядок и обзор —
в `prompts/README.md`; общие конвенции (стек, инварианты, структура) — в
`prompts/_conventions.md`; открытые вопросы — в `prompts/_open-questions.md`.

Порядок применения: 00 (каркас) → 01 (URL-схема pik.ru) → 02 (Criteria) →
03 (справочники) → 04 (regex-правила) → 05 (rapidfuzz-матчинг) → 06 (фасад parse)
→ 07 (url_builder) → 08 (валидатор) → 09 (POST /build-url) → 10 (интеграционные
тесты). Применять последовательно; после каждого шага гонять линт + тесты.

Ключевые инварианты (детали в `prompts/_conventions.md`): никаких LLM/внешних API
в рантайме; единственный сетевой вызов на запрос — валидация выдачи через backend
API pik.ru; нераспознанное всегда уходит в `warnings`, ничего не отбрасывается
молча; справочники — отдельный локальный JSON-слой, обновляемый скриптом.

---

## URL-схема pik.ru — источник правды

**`docs/pik-url-schema.md`** — единственный источник правды по схеме фильтров
`pik.ru/search` (промпт 01, выполнен). На него опираются справочники (03),
`url_builder` (07) и `validator` (08). Центральное правило схемы —
**single-путь / multi-query**: одно значение фильтра → слаг в пути URL,
два и больше → query-параметр с id/GUID через запятую.

Решённые открытые вопросы (из `prompts/_open-questions.md`):

- **№1 (дефолтная сортировка):** если пользователь не указал сортировку,
  `sortBy`/`orderBy` не добавляются вовсе — действует дефолтная выдача сайта
  («рекомендуемые»).

---

## Контракт Criteria и модели API (промпт 02 — выполнен)

- **`app/parsing/schema.py`** — внутренний контракт критериев: pydantic-модель
  `Criteria` (+ `Rooms`, `Sort`, `HousingType`, `MatchedEntity`). На неё
  опираются парсинг (промпты 04–06) и `url_builder` (07). «Пустой» `Criteria()`
  валиден: скаляры — `None`, списки — пустые. Ключевые решения — в докстринге
  модуля: `rooms` — всегда список (single vs multi решает url_builder по длине);
  `finish` — `bool | None` (URL выражает только `True` — слаг `finish`;
  `False` url_builder обязан отправить в warnings); `sort` — строковый ключ
  `price_asc|price_desc|area_asc|area_desc` с properties `field`/`order`
  для `sortBy`/`orderBy`.
- **Модели API** `BuildUrlRequest`/`BuildUrlResponse` живут в `app/main.py`
  (это контракт HTTP-слоя, не парсинга). Ответ: `{url, criteria, result_count,
  warnings}`; человекочитаемое представление критериев для поля `criteria` даёт
  `Criteria.to_public_dict()` (пример: `{"rooms": "2", "price_max": 15000000,
  "metro": ["Аэропорт Внуково"], "finish": true, "sort": "price_asc"}`).
  Поле `text` запроса имеет `examples` — Swagger-форма открывается
  предзаполненной. Тесты контракта — `tests/parsing/test_schema.py`.

---

## Разработка

Каркас проекта собран (промпт 00): FastAPI-app с health-роутом и заглушкой
`POST /build-url` (возвращает 501 до реализации промптов 03–09).
Выполнен промпт 01: спецификация URL-схемы зафиксирована в `docs/pik-url-schema.md`.
Выполнен промпт 02: контракт `Criteria` и модели API (см. раздел выше).

### Стек

Python 3.12 · FastAPI · pydantic v2 · httpx · rapidfuzz · pytest · uv · ruff.
Entrypoint FastAPI объявлен в `pyproject.toml` (`[tool.fastapi] entrypoint = "app.main:app"`).

### Команды

- Установка: `uv sync`
- Запуск dev-сервера: `uv run fastapi dev` (или `uv run uvicorn app.main:app --reload`)
- Тесты: `uv run pytest`
- Линт: `uv run ruff check`; формат: `uv run ruff format` (проверка: `--check`)

Пользовательский интерфейс — Swagger-форма на `http://127.0.0.1:8000/docs`
(своего фронтенда нет).

В ruff отключены RUF001–RUF003 (ambiguous unicode): проект намеренно использует
русский текст в строках, докстрингах и комментариях.

### Карта директорий

```
app/
  main.py            # FastAPI-app, health, POST /build-url (501-заглушка) + модели API
  parsing/           # schema.py — Criteria (контракт, готов); rules.py, entity_match.py, parser.py — заглушки
  reference/         # loader.py, refresh.py — заглушки; сюда лягут JSON-справочники
  pik/               # url_builder.py, validator.py — заглушки
tests/               # pytest; test_health.py — smoke-тесты; parsing/test_schema.py — контракт Criteria/API
docs/                # pik-url-schema.md — спецификация URL-схемы pik.ru (источник правды)
prompts/             # декомпозиция задачи (см. ниже)
```

---

При каждом изменении проекта — изменять и дополнять CLAUDE.md.
