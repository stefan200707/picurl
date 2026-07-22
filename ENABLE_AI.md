# Настройка ИИ-обогащения (ENABLE_AI.md)

Для включения ИИ-обогащения (AI Enrichment) и устранения предупреждения *«ИИ-обогащение выключено — часть запроса не обработана»* настройте переменные окружения в файле `.env`.

Сервис использует `pydantic-settings` и читает `.env` из корня проекта.

## 1. Конфигурация `.env`

Добавьте в `.env`:

```env
# Включение ИИ-обогащения
AI_ENRICHMENT_ENABLED=true

# Выбор провайдера: "antigravity" (Google Gemini) или "claude" (Anthropic)
AI_PROVIDER=antigravity

# Имя модели (по умолчанию gemini-3.5-flash)
AI_MODEL_NAME=gemini-3.5-flash

# При использовании AI_PROVIDER=claude:
# ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Подключение к PostgreSQL + pgvector (Карта памяти)
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5432/picurl_ai
```

---

## 2. Запуск PostgreSQL + pgvector

Для работы карты памяти (семантический кэш и факты) поднимите базовый контейнер и накатите миграцию:

```bash
docker compose up -d postgres
docker exec -i picurl-postgres psql -U postgres -d picurl_ai < app/ai/migrations/01_memory_tables.sql
```

Подробную инструкцию по работе с провайдером **Antigravity** вы найдёте в [ENABLE_ANTIGRAVITY.md](file:///Users/stefan/Desktop/picurl/ENABLE_ANTIGRAVITY.md), а полное руководство по развёртыванию — в [SETUP.md](file:///Users/stefan/Desktop/picurl/SETUP.md).

