# 🏢 picurl — Умный генератор ссылок ПИК

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi)](https://fastapi.tiangolo.com)
[![uv](https://img.shields.io/badge/uv-Fast-purple.svg)](https://docs.astral.sh/uv/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-pgvector-blue.svg)](https://github.com/pgvector/pgvector)

**picurl** — это гибридный микросервис, который переводит свободные текстовые запросы на русском языке (например: _«хочу двушку у метро, до 15 млн, с отделкой, близко к центру»_) в точные ссылки на [pik.ru](https://www.pik.ru) с уже проставленными фильтрами и параметрами.

Сервис использует **гибридный пайплайн**:
- **Детерминированный парсинг**: быстрый и безопасный разбор регулярными выражениями и нечетким поиском (`rapidfuzz`) по локальным JSON-справочникам.
- **ИИ-обогащение (опционально)**: подключение LLM (Antigravity / Gemini или Claude) для сложного пространственного и контекстного поиска ("рядом с школами", "в центре Москвы").
- **Самообучение**: накопление знаний в векторной памяти (`pgvector`) и их автоматический промоушен в быстрые локальные справочники.

Подробное пошаговое руководство по настройке вы найдете в **[SETUP.md](SETUP.md)**, а
пошаговый онбординг именно по ИИ-обогащению через Antigravity (Gemini) — в
**[ENABLE_ANTIGRAVITY.md](ENABLE_ANTIGRAVITY.md)**.

---

## 🚀 Быстрый старт (Запуск проекта)

### 1. Установка
Для работы требуется Python 3.12+ и пакетный менеджер [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

### 2. Запуск базы данных (для ИИ-обогащения)
ИИ-обогащение — опциональный слой; базовый пайплайн (парсинг → URL → валидация)
работает без БД. Если он нужен, запустите PostgreSQL с расширением `pgvector`
через Docker и накатите миграции по порядку (карта памяти + наблюдаемость вызовов
ИИ + признак неудачной попытки):
```bash
docker compose up -d postgres
docker exec -i picurl-postgres psql -U postgres -d picurl_ai < app/ai/migrations/01_memory_tables.sql
docker exec -i picurl-postgres psql -U postgres -d picurl_ai < app/ai/migrations/02_ai_call_log.sql
docker exec -i picurl-postgres psql -U postgres -d picurl_ai < app/ai/migrations/03_ai_call_failed.sql
```

### 3. Настройка окружения (`.env`)
Создайте файл `.env` в корне проекта (полный список переменных — в `SETUP.md`):
```env
AI_ENRICHMENT_ENABLED=true
AI_PROVIDER=antigravity
AI_MODEL_NAME=gemini-3.5-flash
DATABASE_URL=postgresql://postgres:password@localhost:5432/picurl_ai
INTERNAL_REFRESH_TOKEN=your_super_secret_token_here
```

### 4. Запуск сервера
```bash
uv run fastapi dev
```
Сервер поднимется по адресу: **http://127.0.0.1:8000**

### 5. Как пользоваться (Swagger UI)
Интерактивная работа с API происходит через встроенную Swagger-форму:
1. Откройте в браузере **<http://127.0.0.1:8000/docs>**
2. Раскройте метод `POST /build-url` и нажмите кнопку **Try it out**.
3. Введите ваш запрос в поле `text` (например: `"двушка на высоком этаже, до 20 лямов, без отделки"`).
4. Нажмите **Execute**.

**Пример ответа:**
```json
{
  "url": "https://www.pik.ru/search/...",
  "criteria": {
    "rooms": ["2"],
    "price_max": 20000000,
    "finish": false,
    "not_first_floor": true
  },
  "result_count": 142,
  "warnings": [],
  "ai_used": true,
  "ai_failed": false,
  "ai_cache_hit": false,
  "ai_explanation": "Подобраны ЖК в центральных районах."
}
```

### 6. Консольный клиент на C++ (прототип)

Тот же `POST /build-url` из командной строки, без Swagger: однофайловый клиент
на libcurl + nlohmann/json — `cpp_client/build_url_client.cpp`. Зависимости
системные (в репозиторий не тащатся), сборка одной командой `g++` и пример
запуска — в **[cpp_client/README.md](cpp_client/README.md)**. Прототип
показывает, что контракт `BuildUrlResponse` собирается на C++ вручную;
на Python-пайплайн он не влияет и в `uv run pytest` не участвует.

---

## 🛠 Разработка и обслуживание

### Запуск автотестов
```bash
uv run pytest
```

### Проверка и форматирование кода (перед коммитом)
```bash
uv run ruff check           # проверка стиля
uv run ruff format --check  # проверка форматирования (как в CI)
uv run ruff format          # автоматическое форматирование
uv run pytest               # запуск автотестов
```

### Обновление справочников
```bash
# Основные справочники ПИК (ЖК, округа, районы, метро)
uv run python -m app.reference.refresh

# Гео-данные POI (парки, школы из OpenStreetMap)
uv run python -m app.geo.refresh_poi
```
Тот же скрипт `refresh` доступен и по HTTP: `POST /internal/refresh-dicts` с
заголовком `X-Internal-Token`, совпадающим с `INTERNAL_REFRESH_TOKEN` из `.env`.

### Промоушен знаний ИИ (Самообучение)
```bash
uv run python -m app.ai.promotion            # промоушен фактов/алиасов в JSON-справочники
uv run python -m app.ai.promotion --report   # только отчёт по неоднозначным алиасам
```

### Наблюдаемость вызовов ИИ
```bash
uv run python -m app.ai.usage_report            # сколько запросов дошло до ИИ, cache-hit, польза
uv run python -m app.ai.usage_report --days 7    # за последние N дней
```

---

## 📁 Структура проекта

```text
.
├── app/
│   ├── main.py           # Точка входа FastAPI-приложения, health-роут
│   ├── config.py         # pydantic-settings: чтение .env (AI_*, DATABASE_URL, токены)
│   ├── api/              # endpoints.py (POST /build-url, /internal/refresh-dicts),
│   │                     # schemas.py (BuildUrlRequest/BuildUrlResponse)
│   ├── ai/               # ИИ-слой: client.py (Claude/Antigravity), enrichment.py,
│   │                     # prompts.py, embeddings.py, memory.py (pgvector),
│   │                     # promotion.py (самообучение), usage_report.py (наблюдаемость),
│   │                     # migrations/ (SQL-миграции)
│   ├── geo/              # distance.py (haversine), poi.py/refresh_poi.py (OSM),
│   │                     # candidates.py (шорт-лист ЖК для ИИ)
│   ├── knowledge_base/   # Заготовка для локального векторного хранилища/весов
│   ├── parsing/          # schema.py (Criteria), parser.py (фасад parse),
│   │                     # entity_match.py (rapidfuzz), rules/ (regex-правила), stopwords.py
│   ├── reference/        # *.json справочники (метро/округа/районы/ЖК/опции/ориентиры)
│   │                     # + loader.py, refresh.py
│   └── pik/              # url_builder.py (генератор URL) и validator.py (проверка)
├── tests/                # pytest: parsing/, reference/, geo/, ai/, pik/, api/,
│                         # integration/ (E2E), test_health.py (smoke)
├── docs/                 # pik-url-schema.md (URL-схема), ai-enrichment-architecture.md
├── prompts/              # Декомпозиция задачи на пронумерованные промпты сборки
├── SETUP.md              # Подробная инструкция по развертыванию
└── ENABLE_ANTIGRAVITY.md # Инструкция по настройке провайдера Antigravity
```

