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

Подробное пошаговое руководство по настройке вы найдете в **[SETUP.md](file:///Users/stefan/Desktop/picurl/SETUP.md)**.

---

## 🚀 Быстрый старт (Запуск проекта)

### 1. Установка
Для работы требуется Python 3.12+ и пакетный менеджер [uv](https://docs.astral.sh/uv/).

```bash
uv sync
```

### 2. Запуск базы данных (для ИИ-обогащения)
Запустите PostgreSQL с расширением `pgvector` через Docker:
```bash
docker compose up -d postgres
docker exec -i picurl-postgres psql -U postgres -d picurl_ai < app/ai/migrations/01_memory_tables.sql
```

### 3. Настройка окружения (`.env`)
Создайте файл `.env` в корне проекта:
```env
AI_ENRICHMENT_ENABLED=true
AI_PROVIDER=antigravity
AI_MODEL_NAME=gemini-3.5-flash
DATABASE_URL=postgresql://postgres:password@localhost:5432/picurl_ai
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
  "ai_cache_hit": false,
  "ai_explanation": "Подобраны ЖК в центральных районах."
}
```

---

## 🛠 Разработка и обслуживание

### Запуск автотестов
```bash
uv run pytest
```

### Проверка и форматирование кода
```bash
uv run ruff check
uv run ruff format
```

### Обновление справочников
```bash
# Основные справочники ПИК (ЖК, округа, районы, метро)
uv run python -m app.reference.refresh

# Гео-данные POI (парки, школы из OpenStreetMap)
uv run python -m app.geo.refresh_poi
```

### Промоушен знаний ИИ (Самообучение)
```bash
uv run python -m app.ai.promotion
```

---

## 📁 Структура проекта

```text
.
├── app/
│   ├── main.py            # Точка входа FastAPI, эндпоинт POST /build-url
│   ├── api/               # Pydantic-модели API (schemas.py) и роуты
│   ├── ai/                # ИИ-слой (клиенты LLM, промоушен, векторы, миграции)
│   ├── geo/               # Гео-вычисления и интеграция с OpenStreetMap POI
│   ├── knowledge_base/    # Хранилище векторных знаний
│   ├── parsing/           # Regex-правила извлечения фактов и fuzzy-матчер
│   ├── reference/         # Локальные JSON-справочники + скрипт обновления
│   └── pik/               # url_builder (генератор URL) и validator (проверка)
├── tests/                 # Автоматические unit и E2E тесты
├── docs/                  # Документация (URL-схема, архитектура ИИ)
├── SETUP.md               # Подробная инструкция по развертыванию
└── ENABLE_ANTIGRAVITY.md  # Инструкция по настройке провайдера Antigravity
```

