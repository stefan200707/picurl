# 00 — Каркас проекта (scaffold)

> Прочитай `_conventions.md` перед началом.

## Контекст

Репозиторий `picurl` пуст (только `CLAUDE.md` и `.claude/`). Нужно поднять каркас
проекта под стек Python 3.12 / FastAPI / pydantic v2 / httpx / rapidfuzz / pytest,
управляемый через `uv`, линтуемый `ruff`.

## Цель

Готовый к разработке скелет: структура пакетов, зависимости, конфиги, пустой
работающий FastAPI-эндпоинт-заглушка, проходящий тест «сервис поднимается».

## Что сделать

1. **`pyproject.toml`** (управляется `uv`):
   - `requires-python = ">=3.12"`.
   - Зависимости: `fastapi`, `pydantic>=2`, `httpx`, `rapidfuzz`, `uvicorn`.
   - Dev-зависимости: `pytest`, `pytest-asyncio`, `ruff`.
   - Секция `[tool.ruff]` (line-length, select правил) и `[tool.pytest.ini_options]`.
   - Объяви entrypoint FastAPI (`[tool.fastapi] entrypoint = "app.main:app"`).
2. **Структура пакетов** (пустые модули с `__init__.py` и TODO-заглушками, чтобы
   импорты работали): согласно целевой структуре из `_conventions.md`
   (`app/`, `app/parsing/`, `app/reference/`, `app/pik/`, `tests/`, `docs/`).
3. **`app/main.py`** — минимальный FastAPI-app с health-заглушкой и
   зарегистрированным (пока заглушечным) роутером `POST /build-url`, который
   принимает `{ "text": str }` и возвращает `501`/`NotImplemented` или пустую
   валидную структуру ответа. Реальную логику подключат следующие промпты.
4. **`README.md`** проекта: краткое описание, как установить (`uv sync`), как
   запустить (`fastapi dev` / `uvicorn`), как гонять тесты (`pytest`),
   как линтовать (`ruff check` / `ruff format`).
5. **`tests/test_health.py`** — тест, что приложение импортируется и health-роут
   отвечает 200 (через `fastapi.testclient` / `httpx`).
6. **`.gitignore`** для Python (venv, `__pycache__`, `.pytest_cache`, `.ruff_cache`).

## Требования к результату

- `uv sync` устанавливает окружение без ошибок.
- `pytest` — зелёный (минимум тест health).
- `ruff check` и `ruff format --check` — чисто.
- Импорт `app.main:app` работает.

## Обнови CLAUDE.md

Добавь раздел «Разработка»: стек, команды установки/запуска/тестов/линта,
краткая карта директорий.

## Зависит от

Ничего (первый шаг).
