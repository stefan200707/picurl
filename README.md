# picurl — pik.ru URL builder

Сервис принимает свободный текст на русском языке о желаемой квартире
(«хочу двушку у метро, до 15 млн, с отделкой») и возвращает рабочую ссылку на
[pik.ru](https://www.pik.ru) с уже применёнными фильтрами и сортировкой —
чтобы не тыкать вручную по чекбоксам на сайте.

Парсинг полностью детерминированный (regex + rapidfuzz по локальным
JSON-справочникам), без LLM и внешних API в рантайме.

## Установка

Требуется Python 3.12+ и [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

## Запуск

```bash
uv run fastapi dev        # dev-сервер с автоперезагрузкой
# или
uv run uvicorn app.main:app --reload
```

### Как пользоваться

Своего фронтенда у сервиса нет — вводить текст и получать ссылку удобнее всего
через **Swagger-форму на <http://127.0.0.1:8000/docs>**:

1. Раскройте `POST /build-url` и нажмите **Try it out**.
2. Заполните поле `text` (например: `двушка у метро, до 15 млн, с отделкой`).
3. Нажмите **Execute** — в ответе придёт JSON со ссылкой на pik.ru.

## Тесты

```bash
uv run pytest
```

## Линт и форматирование

```bash
uv run ruff check
uv run ruff format
```

## Структура

```
app/
  main.py            # FastAPI, POST /build-url
  parsing/           # regex-правила, fuzzy-матчинг, Criteria, фасад parse()
  reference/         # локальные JSON-справочники + loader/refresh
  pik/               # url_builder (Criteria -> URL), validator (запрос к API pik.ru)
tests/               # pytest-тесты, зеркалят структуру app/
docs/                # спецификации (URL-схема pik.ru)
prompts/             # декомпозиция задачи на пошаговые промпты
```
