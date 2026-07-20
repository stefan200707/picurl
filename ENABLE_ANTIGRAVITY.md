# Инструкция по запуску ИИ-обогащения через модель Antigravity (Gemini)

Для того чтобы включить ИИ-обогащение через провайдера **antigravity** (Google Gemini), вам необходимо задать соответствующие переменные окружения.

Сервис использует `pydantic-settings` и читает файл `.env` из корневой директории проекта.

Создайте файл `.env` (если его нет) в корневой папке проекта и добавьте следующие переменные:

```env
# Включает ИИ-обогащение
AI_ENRICHMENT_ENABLED=true

# Указывает использование провайдера antigravity
AI_PROVIDER=antigravity

# Ключ от API Gemini, необходимый для работы модели antigravity
GEMINI_API_KEY=your_gemini_api_key_here

# (Опционально) URL подключения к базе данных PostgreSQL с pgvector для работы карты памяти ИИ
# Пример: postgresql://postgres:postgres@localhost:5432/picurl
DATABASE_URL=your_database_url_here
```

После добавления ключа и изменения провайдера, перезапустите сервер:

```bash
uv run fastapi dev
```

ИИ-обогащение начнёт работать через агента Antigravity (Gemini), обрабатывая сложные контекстные запросы.
