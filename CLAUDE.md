# CLAUDE.md

Проект: **picurl** (GitHub: stefan200707/picurl).

> Это карта и правила решений — то, что нужно в **любой** задаче.
> Мотивация конкретных регексов, пост-мортемы дефектов и хронология —
> в `docs/` (см. «Источники правды»). Правила обновления файла — в конце.

## Навигация по коду

**Вопрос о коде → сперва `graphify query "<вопрос>"`** (дешевле grep и
`GRAPH_REPORT.md`). Также `graphify path "<A>" "<B>"`, `graphify explain "<концепт>"`.
Навигация — `graphify-out/wiki/index.md`. После изменений кода — `graphify update .`.

## Описание

Микросервис: превращает свободный русский текст о желаемой квартире
(«хочу двушку у метро до 15 млн») в рабочую ссылку `pik.ru/search` с применёнными
фильтрами. Логика **детерминированная**: regex + rapidfuzz по локальным JSON-справочникам.
ИИ — опциональный ограниченный fallback, в рантайме не обязателен. Интерфейс — Swagger UI
(`http://127.0.0.1:8000/docs`), своего фронтенда нет. pik.ru продаёт только новостройки.

## Стек и конвенции

- Python 3.12+, FastAPI + Uvicorn, менеджер пакетов `uv`.
- `pydantic` v2, `rapidfuzz`, `httpx`. Тесты `pytest` + `pytest-asyncio` (`asyncio_mode=auto`).
- ИИ-стек: `claude-agent-sdk`, `google-antigravity` (CLI `agy`), PostgreSQL 15+ с pgvector,
  `sentence-transformers`, `asyncpg`, `pydantic-settings`.
- Строгая типизация и валидация через Pydantic. Длина строки — 100.
- Ruff select: `E`, `W`, `F`, `I`, `UP`, `B`, `SIM`, `C4`, `RUF`. Кириллица в строках
  намеренная — `RUF001/002/003` отключены.

## Команды

```
uv sync                                          # зависимости
uv run fastapi dev                               # dev-сервер
uv run pytest                                    # тесты (гоняют оба QA-корпуса)
uv run ruff check | uv run ruff format [--check] # линт | формат

docker compose up -d postgres                    # БД ИИ (pgvector)
psql $DATABASE_URL -f app/ai/migrations/01_memory_tables.sql   # и 02_*, 03_*

uv run python -m app.reference.refresh           # JSON-справочники (api.pik.ru)
uv run python -m app.reference.refresh_metro_geo # координаты/линии метро из OSM
uv run python -m app.reference.refresh_mkad      # полигон МКАД (снапшот — приближение)
uv run python -m app.geo.refresh_poi             # POI-кэш (--force = всё; без флага — v1 и отсутствующие)

uv run python -m app.ai.usage_report [--days N]  # отчёт вызовов ИИ
uv run python -m app.ai.promotion [--report]     # неоднозначные алиасы
uv run python scripts/parse_audit.py             # аудит парсера (198 коротких)
uv run python scripts/complex_audit.py           # аудит полного пути (31 длинный)
uv run python scripts/landmark_alias_audit.py    # падежное покрытие ориентиров
uv run python scripts/outcome_audit.py           # аудит ВЫДАЧИ: ссылка ↔ значения в карточках (сеть, не pytest)
uv run python scripts/reference_audit.py         # аудит справочных id: живой/мёртвый/невалидный (сеть, не pytest)
```

## Структура каталогов

```text
app/
  main.py            # FastAPI-app, health, POST /build-url
  warnings.py        # TaggedWarning/WarningCategory/WarningSeverity — категории warnings (Г6)
  api/               # endpoints.py + pydantic-модели API (schemas.py)
  parsing/           # schema.py — Criteria; rules/ — regex-правила; parser.py — фасад parse;
                     #   entity_match.py — rapidfuzz-матчинг; stopwords.py
  reference/         # *.json справочники; loader.py — загрузка/кэш; refresh*.py
  pik/               # url_builder.py; validator.py; id_trust.py; location_fallback.py
  geo/               # distance.py (haversine); candidates.py (шорт-лист); poi.py; mkad.py; refresh_poi.py
  ai/                # client.py; enrichment.py (enrich/resolve_options, гейты); embeddings.py;
                     #   memory.py (pgvector); promotion.py; usage_report.py; prompts.py; schema.py
  knowledge_base/    # локальное векторное хранилище/кэш ИИ
tests/               # integration/ — E2E; parsing/ reference/ geo/ ai/;
                     #   corpus/queries.txt (198 коротких) + corpus/complex_queries.txt (36 длинных);
                     #   test_parse_audit_regression.py; test_complex_queries_regression.py;
                     #   test_warning_categories.py (категории warnings)
docs/                # см. «Источники правды»
prompts/             # декомпозиция задачи (00→10, README, _conventions, _open-questions)
scripts/             # parse_audit.py; complex_audit.py; landmark_alias_audit.py; cleanup_metro_duplicates.py
```

Порядок сборки (`prompts/`): 00 каркас → 01 URL-схема → 02 Criteria → 03 справочники →
04 regex → 05 rapidfuzz → 06 фасад parse → 07 url_builder → 08 валидатор →
09 POST /build-url → 10 E2E. Инварианты сборки — `prompts/_conventions.md`.

## Поток обработки запроса

`POST /build-url`, поле `text`:

1. `parse(text)` → `Criteria` + `warnings` (+ `option_candidates`).
2. **ИИ-обогащение (опционально)** — при выключенном ИИ или ошибке процесс продолжается
   с исходными criteria. Гейты и ветки — `docs/ai-enrichment-architecture.md`.
3. `build_url(criteria, warnings=None)` → URL. Рантайм обязан передавать `warnings`.
4. `validate(criteria)` → проверочный запрос к `api.pik.ru/v2/filter` (единственный
   сетевой вызов рантайма, best-effort). 0 результатов → warning.

Ответ: `{url, criteria, result_count, warnings, warnings_detailed, ai_used, ai_failed,
ai_cache_hit, ai_explanation}`. `ai_used=True` = ИИ **реально повлиял**; `ai_failed=True` = попытка
была и упала (**любая** ветка ИИ, включая free-text до гейтов); оба `False` = ИИ не
звали вовсе (гейт / выключен / нечего обогащать). Провал = модель **не ответила**:
исключение вызова или cooldown circuit breaker'а. «Ответила и ничего не заполнила»
и «значения отбиты валидацией» — успешный вызов без пользы (`ai_failed=False`),
различает лог. Нет кредов / ИИ выключен — не провал (инвариант 9).

## Категории warnings (задача Г6, внедряется порциями)

Дизайн целиком — **`docs/warnings-severity-proposal.md`** (там же статус порций).
Реализованы шаги 1-2 из пяти.

- **`warnings` — не выходной буфер, а внутренний канал данных.** Один мутируемый список
  передаётся по ссылке; `enrichment` удаляет из него элементы **по точному равенству
  строк**, `_residual_fragments` разбирает строки регексом, `scripts/parse_audit.py` матчит
  суффикс «не удалось распознать, не попало в ссылку» — и на нём держатся пороги QA.
  Поэтому носитель категории — **подкласс `str`** (`app/warnings.py::TaggedWarning`):
  несёт `category`, но для `==`, `in`, `remove()`, регекса, `deepcopy`, `json` и pydantic
  остаётся строкой. Ломать эту совместимость нельзя — ошибка была бы молчаливой.
- **Категорий семь** (`WarningCategory`): `lost` (распознали, но в ссылку не доехало) /
  `unknown` (природа неизвестна) / `noise` (корректно отброшено) / `capped` (такого фильтра
  у pik.ru нет) / `unverified` (`result_count` фильтр не учитывает) / `degraded` (не
  применили по своей вине — повтор осмыслен) / `info` (справка о сужении). `severity` —
  **производная**: `lost`/`unknown` → `error`, `degraded`/`capped` → `warning`, остальные →
  `info`.
- **Неразмеченная точка = `unknown`** — нормальный промежуточный статус, а не дефект:
  размечено пока только `parser._classify_residual`, остальные ~61 точка ждут порций 3-5.
- **`warnings_detailed` собирается ДО конструирования `BuildUrlResponse`** — pydantic v2
  коэрсит подкласс `str` к обычному `str`, внутрь модели категория не доезжает. Плоский
  `warnings` **выводится из** `warnings_detailed`, чтобы источник правды был один.
- **Новая строка категорию не наследует** (конкатенация, срез, `.strip()`, f-строка). На
  этом канале таких операций нет; правило зафиксировано в докстринге `app/warnings.py`.
- **Различитель остатка асимметричен намеренно** (`parser._classify_residual`): `lost` — по
  предметному признаку (слово-параметр или число с единицей), `noise` — только если
  **каждое** слово приветствие/вежливость/стоп-слово, всё спорное → `unknown`. `unknown` и
  `lost` дают один severity, спутать их дёшево; `noise` на реальной потере гасит сигнал и
  хуже плоского списка. Приветствия — **отдельный** `_GREETING_WORDS`, не `STOP_WORDS`:
  тот переиспользуется `entity_match`, и пополнение изменило бы поведение парсинга.

## Контракты

- **`app/parsing/schema.py`** — pydantic-модель `Criteria` (+ `Rooms`, `Sort`, `HousingType`,
  `Finish`, `POIRequirement`, `LandmarkRequirement`, `StationClassRequirement`). Пустой
  `Criteria()` валиден. `rooms` — всегда список (single vs multi решает url_builder).
  `finish` — `list[Finish]`. `sort` — ключ `price_asc|price_desc|area_asc|area_desc`.
  `to_public_dict()` — человекочитаемое представление. `location_query_dict()` — общий сбор
  id локаций (переиспользуют url_builder и validator).
- **Справочники** `app/reference/*.json`: `metro`, `counties`, `districts`, `complexes`,
  `benefits`, `option_groups`, `options`, `landmarks`, `mkad_ring`. Формат записи — `RefEntry`
  (`loader.py`): `{name, slug, id, aliases[]}`, `id` строкой. Нужны **обе URL-формы**:
  `slug` (single → путь), `id`/GUID (multi → query). Загрузка — `load_all() -> ReferenceData`,
  кэш `functools.cache` + `clear_cache()`; точный поиск `find_by_name()` c `normalize()`.
- `refresh` перезаписывает только `complexes/counties/metro/districts` (мёрж сохраняет
  кураторские поля). Эндпоинт `/internal/refresh-dicts` защищён `X-Internal-Token`
  (env `INTERNAL_REFRESH_TOKEN`).

## Инварианты (нарушать нельзя)

1. **Ничего не отбрасывается молча.** Любой нераспознанный/неподдерживаемый фрагмент →
   `warnings` (или `option_candidates`). Span покрывает и «проигравший» фрагмент.
2. **Single-путь / multi-query** — одно значение → слаг в пути, два и более → query
   с id/GUID через запятую. Источник правды — `docs/pik-url-schema.md`.
3. **LLM не считает дистанции и не выдумывает фильтры.** Гео-сужение — чистый haversine;
   координаты уходят модели лишь как факт, обратно не принимаются. Любой ответ ИИ
   валидируется против справочника (`sanitize_against_shortlist`, `sanitize_option_resolution`,
   `sanitize_landmark_resolution`); выдуманное отбрасывается, фраза остаётся в `warnings`.
4. **Мёрж не затирает кураторские `name`/`slug`/`id`/`aliases`.** Стабильная сортировка.
5. **Слияние записи с непустым `id`/`slug` в другую ЗАПРЕЩЕНО** — теряет её GUID.
   Каждый GUID станции = отдельное значение фильтра `metroStations` (разные платформы
   одного узла); потеря сужает выдачу до одной платформы. `cleanup()` в
   `scripts/cleanup_metro_duplicates.py` обязан падать `ValueError` при `dup.id`/`dup.slug`.
6. **Runtime читает JSON только с диска.** Сеть — только `refresh*` и валидатор.
7. **Отсутствующая URL-форма (`slug`/`id`) → `warning`, не молчание.**
8. **`agy` — всегда `--print`, НИКОГДА `--dangerously-skip-permissions`** (сырой
   `user_query` — поверхность prompt injection).
9. **Нет учётных данных ИИ = ИИ-слой выключен (не ошибка);** базовый пайплайн работает.
10. **Промоушен факта — по числу независимых наблюдений** (одна уверенная галлюцинация не
    промоутит). Алиас — только при единогласии фразы→slug; расходящиеся не промоутятся.
11. **Гомоглифы латиница→кириллица нормализуются 1:1** (длина строки не меняется).
12. **`fully_resolved` консервативен** — любой неизвестный факт уводит в ИИ, не додумывает.
13. **Усечение до лимита — ПОСЛЕ сортировки по дистанции** (везде в ранжирующем коде).
14. **Дистанции фолбэка — реальный haversine**, не шаблонный текст.
15. **`aclosing` вокруг стрима SDK** — иначе недоеденный async-генератор добивается GC
    на живом loop (`RuntimeError` + утечка процесса CLI).
16. **Station-class фолбэк только при дефолтном радиусе** (явная `max_distance_m` = жёсткая
    отсечка). Данные reference — только из официальных источников, GUID/id не подбирать.

## Гео-сужение: правило AND

Гео-фолбэк **ПЕРЕСЕКАЕТСЯ** с уже выбранными ЖК, а не объединяется: оба списка сужают
одну ось («какие ЖК»), OR стирал более узкое требование. Пустое пересечение — **не** повод
расширяться: оставляем более специфичный список + warning «гео-условия не пересекаются».
Общая логика — `app.pik.location_fallback.combine_with_fallback`; **и `build_url`, и
`validate` обязаны проходить через неё** (раньше расходились, `result_count` завышался
в 6.5 раза). Флаг `Criteria.complexes_matched_empty` отличает «посчитали и получили ноль»
от «не считали вовсе» — служебный, в `to_public_dict()`/`to_query_dict()` не попадает.

Подробности механик (ориентиры, суперлативы, класс станций, МКАД) —
`docs/geo-narrowing.md`.

## Потолок — свойство ВЫБРАННОГО эндпоинта, а не pik.ru

У ПИК **два** бэкенда фильтрации, и ограничения у них разные:

| | `api.pik.ru/v2/filter` — куда ходит наш валидатор | `flat.pik-service.ru/api/v1/filter/block-with-flats` — чем живёт сайт |
|---|---|---|
| `metroStations`/`districtLocations`/`districtCounties` | игнорирует | **применяет** |
| `hasFinish` | применяет (только `1\|2\|3`) | применяет (и `0`, и списки) |
| `settlementYear*` / `Month*` | игнорирует | **тоже игнорирует** |
| `rooms=3`, `2,3` | **HTTP 500** | 200 |

Поэтому `validator.py` держит `UNVERIFIED_LOCATION_PARAMS` (поле
`location_filters_not_verified`) и `UNVERIFIED_NON_LOCATION_PARAMS`;
`UNVERIFIED_BY_BACKEND_PARAMS` — их сумма. **Пока рантайм ходит в `v2/filter`,
`result_count` при локационном фильтре ничего не доказывает.**

**Отделка исправлена 2026-07-28:** валидатор шлёт `hasFinish` (а не несуществующий
`finish`) и потому реально проверяет одиночные `1|2|3`; ноль и списки в запрос не
уходят вовсе и остаются помеченными — их `v2/filter` либо игнорирует, либо применяет
не как OR. Признак непроверяемости у отделки — отдельный флаг, а не «параметр ушёл в
запрос». **Срок сдачи не применяется нигде** — единственный из пяти, где прежнее
утверждение устояло (`settlementMonth*` дописаны в константу).
**Справочники сверены с настоящим эндпоинтом 2026-07-28** (`scripts/reference_audit.py`,
вариант Б шага J — только аудит, рантайм не мигрирует: эвристика отбора UA не понята,
а деградирует молча). Из **176** локационных id живы **158**: `metroStations` 58/61,
`districtLocations` 33/34, `blocks` 67/71, `districtCounties` **0/10**. Невалидных нет
ни одного — все потери вида «id признан, предложений нет». `counties.json` содержит
**чужую номенклатуру**: фасет отдаёт другой ряд id, 7 замен выводятся автоматически,
2 требуют ручной сверки, ЦАО/Троицкий АО/Щербинка не выводятся ничем. Проверка — только
**сужением выдачи**: отсутствие id в фасете невалидность не доказывает. Разбор, вердикты
и оба рубежа детектора заглушки — `docs/measurements/2026-07-28-reference-audit.md`.

## Пороги

Env-настраиваемые ключи — `app/config.py`. Гео-пороги и лимиты — **модульные константы**:
`app/geo/candidates.py` (`SHORTLIST_LIMIT`, `LANDMARK_*`, `STATION_CLASS_*`),
`app/geo/distance.py` (`CENTER_RADIUS_M`), `app/parsing/entity_match.py`
(`OPTION_MIN_QRATIO`), `app/parsing/parser.py` (`MAX_OPTION_CANDIDATE_WORDS`).
**Значения смотреть в коде, не здесь** — дубль в документации устаревает молча.

Калибруемые эвристики (радиусы, порог семантического кэша) — ужесточать по мере улучшений.
Обоснования текущих значений — `docs/thresholds-rationale.md`.

## QA-харнесс

Два корпуса, дополняют друг друга, гоняются в каждом `pytest`:

1. **Парсер** — `tests/corpus/queries.txt` (198 коротких), `scripts/parse_audit.py`
   (офлайн, без сети и ИИ), `tests/test_parse_audit_regression.py`.
   Пороги: crashes=0, empty≤28, no-filter-URL≤61, coverage≥0.91.
2. **Полный детерминированный путь** — `tests/corpus/complex_queries.txt` (31 длинный;
   **индексы 0/1 и последнюю строку не сдвигать** — на них опираются точечные тесты),
   `scripts/complex_audit.py` (`parse`→`enrich` при `AI_ENRICHMENT_ENABLED=False`→`build_url`).
   Пороги: crashes=0, URL-без-фильтров=0, без `blocks`≤8, coverage≥0.92, фильтров≥8.93.

**Зачем второй:** первый не зовёт `enrich` и не видит участок, где живёт `blocks=` — там
фильтр может быть распознан и не доехать до ссылки.

**Чего не видят ОБА:** дефект формы, отсутствующей в корпусах, и дефект, который
не теряет фильтр, а **добавляет лишний** — сводные метрики от такого только «улучшаются».
Ловится лишь живым прогоном с проверкой ссылки на pik.ru, в формате
**«запрос → ссылка → критерии → ЖК с дистанциями → warnings»**.

**Пороги ослаблять только с обоснованием в `docs/history.md`.** За историю проекта
обоснованное снижение было ровно одно (9.03→8.93, удаление трёх сфабрикованных
`timeOnFoot`). Метрика «фильтров на запрос» не отличает честный фильтр от выдуманного.

## Источники правды

| Файл | Содержание |
|---|---|
| `docs/pik-url-schema.md` | URL-схема `pik.ru/search` — единственный источник правды по фильтрам |
| `docs/ai-enrichment-architecture.md` | Поток ИИ, гейты, ведро C, наблюдаемость (§8, §8.4) |
| `docs/parsing-rationale.md` | Коллизии спанов, guard'ы, fuzzy-матчинг опций, каталог неподдерживаемого |
| `docs/geo-narrowing.md` | Ориентиры, суперлативы, класс станций, МКАД, `id_trust` |
| `docs/poi-cache.md` | Схема v2, «дистанция только по действующим», совместимость с v1 |
| `docs/external-facts.md` | OSM-теги, зеркала Overpass, потолок данных, квоты ИИ |
| `docs/data-allowlists.md` | Удалённые/восстановленные GUID, коллизии алиасов, metro-integrity |
| `docs/thresholds-rationale.md` | Обоснования порогов и лимитов |
| `docs/outcome-audit.md` | Аудит выдачи: сверка ссылки с карточками, его границы |
| `docs/measurements/2026-07-28-reference-audit.md` | Аудит справочных id, разбор округов, детектор заглушки |
| `docs/history.md` | Полная хронология милстоунов и пост-мортемов |
| `ENABLE_ANTIGRAVITY.md` | Онбординг ИИ/БД |

---

## Правила обновления этого файла

CLAUDE.md грузится в **каждый** запрос — его размер оплачивается всегда. Поэтому:

- **По умолчанию изменения кода документируются в `docs/*.md`, не здесь.** Разбор
  дефекта, мотивация регекса, замер, пост-мортем → соответствующий файл `docs/`
  (+ хронология в `docs/history.md`).
- **CLAUDE.md правится только если изменилось одно из:** команда, дерево каталогов,
  инвариант, контракт (`Criteria`/API/`RefEntry`), поток обработки, порог QA,
  список источников правды.
- **Ничего не дописывать в конец «на всякий случай».** Новый абзац здесь — это налог
  на каждую будущую задачу. Если сомневаешься — в `docs/`.
- Ориентир объёма — **до 250 строк**. Файл перерос → выносить в `docs/`, не сокращать
  инварианты.
