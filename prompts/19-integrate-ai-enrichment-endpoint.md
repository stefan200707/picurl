# 19 — Подключение ИИ-обогащения к `POST /build-url` — Milestone AI-4

> Прочитай `_conventions.md`, `docs/ai-enrichment-architecture.md`, промпты
> 15–18 перед началом.

## Контекст

Все компоненты готовы (гео-слой, карта памяти, клиент ИИ) — остаётся
встроить их в существующий пайплайн `app/api/endpoints.py` (промпт 09) так,
чтобы:

- запросы без гео/POI-семантики (все кейсы промптов 00–14) вели себя
  **побитово так же, как раньше** — ни один существующий тест не должен
  измениться;
- запросы с `poi_requirements`/`center_requested` получали дополнительный шаг
  между `parse()` и `build_url()`.

## Задача

### 1. Изменить `POST /build-url` (`app/api/endpoints.py`)

```python
parse_result = parse(text)
criteria = parse_result.criteria
warnings = parse_result.warnings.copy()

ai_meta = AIMeta()  # ai_used=False, cache_hit=False, explanation=None по умолчанию
if criteria.poi_requirements or criteria.center_requested:
    enrichment = await enrich(text, criteria, warnings)
    criteria = merge_enrichment(criteria, enrichment)   # см. ниже
    ai_meta = enrichment.meta

url = pik_build_url(criteria)
...
```

`merge_enrichment` — чистая функция в `app/ai/enrichment.py`: если
`enrichment.matched_complex_ids` непусто, заменяет/сужает
`criteria.complexes` (маппит id из шорт-листа обратно в `MatchedEntity` по
справочнику — координаты/`slug`/`id` уже есть в `complexes.json`, промпт 16).
Если ничего не найдено (ни детерминированно, ни через ИИ) — `criteria` не
меняется, но в `warnings` уже добавлено соответствующее сообщение слоем 18.

### 2. Расширить `BuildUrlResponse` (`app/api/schemas.py`)

Новые **опциональные** поля с дефолтами (обратная совместимость — старые
клиенты Swagger не увидят разницы, если не заглянут в новые ключи):

```python
ai_used: bool = False
ai_cache_hit: bool = False
ai_explanation: str | None = None
```

Обновить docstring/`description` эндпоинта: упомянуть, что при
гео/POI-запросах сервис может (не обязан) обращаться к ИИ, и что это
поведение управляется `AI_ENRICHMENT_ENABLED`.

### 3. Порядок вызовов и цена ошибки

Обогащение выполняется **до** `build_url`/`validate`, потому что оно может
сузить `criteria.complexes`, что влияет на итоговый URL. Если `enrich()`
падает (см. промпт 18 — там уже перехвачено и превращено в warning), эндпоинт
должен продолжить работу с исходным `criteria` (без сужения) — то есть общая
деградация «нет ИИ → шире выдача + warning», а не «нет ИИ → ошибка 500».

### 4. `http_client`/DB-зависимости через FastAPI DI

Как и `get_http_client` (промпт 09), завести `get_memory_pool`/аналог для БД
через `Annotated[..., Depends(...)]`, хранить пул соединений в
`app.state` (поднимать/закрывать в lifespan-хендлере `app/main.py`, по стилю
FastAPI skill — не создавать новый пул на каждый запрос). Если
`AI_ENRICHMENT_ENABLED=False` — пул к БД можно не поднимать вовсе (ленивая
инициализация), чтобы дефолтный запуск сервиса (`uv run fastapi dev`) без
настроенного Postgres не падал при старте.

## Требования к результату

- Полный текущий набор интеграционных тестов (`tests/integration/
  test_build_url_e2e.py`, промпт 10) проходит **без изменений** — это
  критерий, что v1-путь не задет.
- Новые интеграционные тесты (можно в том же файле или
  `tests/integration/test_ai_enrichment_e2e.py`): запрос с «рядом школа и
  детский сад в центре» через `TestClient` с замоканным ИИ-клиентом и
  замоканной/тестовой БД → в ответе `ai_used=True`, `criteria.complexes`
  сужен, `warnings` не содержит ложных «не удалось распознать» на
  POI-фразы (они теперь распознаются `extract_poi_requirements`, промпт 16,
  а не проваливаются в общий remainder).
- `AI_ENRICHMENT_ENABLED=False` (дефолт для CI) — соответствующий тест
  проверяет мягкую деградацию, а не падение сервиса.
- `uv run pytest`, `uv run ruff check` зелёные.

## Обнови CLAUDE.md

Раздел «Поток обработки» (`POST /build-url`): добавить шаг 2.5 «ИИ-обогащение
(опционально)» с описанием условия срабатывания и обратной совместимости.
Обновить пример JSON-ответа в разделе «API», показав новые поля на примере
POI-запроса.

## Зависит от

09, 15, 16, 17, 18.
