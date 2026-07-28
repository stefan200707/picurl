# 27 — Техдолг, ruff-нарушения, документация и гигиена репозитория — Milestone AI-12

> Прочитай `_conventions.md` перед началом. В отличие от 22–26, этот промпт не
> меняет поведение ИИ-слоя — это генеральная уборка по итогам
> `AUDIT_REPORT.md` (2026-07-22), не покрытая остальной серией. Можно
> выполнять в любой момент, параллельно с 22–26 — правки в основном
> изолированные и низкорисковые (кроме п.1, см. ниже).

## Контекст

Независимый аудит репозитория (`AUDIT_REPORT.md`) нашёл 12 находок дублирования/
мёртвого кода, 3 нарушения конфигурации ruff, 5 расхождений в документации и
17 файлов-кандидатов на удаление в корне. Часть находок (архитектура ИИ vs
документация — раздел 1 отчёта) уже адресована промптами 22–26 (см. правки в
этих файлах от 2026-07-22). Этот промпт закрывает всё остальное.

## Задача

### 1. Дублирование и мёртвый код в `app/` (AUDIT_REPORT.md, раздел 2)

Каждый пункт — отдельный маленький коммит/правка, без изменения поведения
(кроме явно отмеченных):

- **2.1** `app/pik/validator.py:54-59` — удалить мёртвый повторный расчёт
  `sortBy`/`orderBy` (уже добавлен `to_query_dict()`).
- **2.2** `app/parsing/schema.py:349-361` (`to_query_dict()`) — заменить
  ручной `if/elif` по `Sort` на `self.sort.field`/`self.sort.order`
  (свойства уже существуют на модели `Sort`).
- **2.3** Вынести сборку `{districtCounties, metroStations, districtLocations,
  blocks}` из `url_builder.py`/`validator.py` в общую функцию (например,
  `Criteria.location_query_dict()`), переиспользовать в обоих местах.
- **2.4** `app/reference/refresh.py` — вынести общий хвост `refresh()` и
  `run_refresh()` (сборка `fetched_by_kind`, merge+write) в приватную
  `_merge_and_write(blocks, data_dir) -> dict[str, int]`.
- **2.5** `app/api/endpoints.py:58` — удалить неиспользуемое
  `ai_meta = AIMeta()` (перезаписывается ниже до чтения).
- **2.6** `app/ai/enrichment.py` (`merge_enrichment`) — поднять дублирующийся
  `from app.parsing.schema import MatchedEntity` один раз в начало функции.
  **Если промпт 25 уже применён к моменту, когда делаешь этот пункт — считать
  выполненным там, не делать дважды.**
- **2.7** `app/parsing/parser.py:117-142` — свести дублирующийся текст
  предупреждения о недостающих `id`/`slug` к одной функции
  `_missing_id_warning(entity_field, entity_name, ent, total_locations) -> str | None`.
- **2.8** Унифицировать источник `DATABASE_URL`: `app/ai/memory.py` читает его
  напрямую через `os.getenv`, `app/config.py::Settings.DATABASE_URL` — мёртвое
  поле. Перевести `memory.py`/`promotion.py`/`main.py` на
  `get_settings().DATABASE_URL` (с тем же дефолтом), убрать модульную
  константу.
- **2.9** `app/geo/distance.py::is_center()` — не вызывается из прод-кода
  (реальный флаг центральности берётся из курируемого `RefEntry.is_center`).
  Либо подключить в `app/reference/refresh.py` как способ проставлять
  первичное значение `is_center` для новых районов, либо явно пометить
  docstring'ом «зарезервировано, см. AUDIT_REPORT.md 2.9» — не оставлять без
  пометки.
- **2.10** `app/parsing/schema.py:339` — заменить `getattr(self,
  "required_tags", None)` на `self.required_tags` (обычное pydantic-поле,
  всегда существует).
- **2.12** `app/geo/refresh_poi.py:17` — переименовать `for complex in
  complexes` в `for block in complexes` (не затенять builtin `complex`).

(2.11 — `POIRequirement.max_distance_m` — уже перенесено в промпт 22, не
делать здесь повторно.)

### 2. Нарушения ruff (AUDIT_REPORT.md, раздел 3)

- **3.1** Разбить длинные regex-строки (>100 символов) в
  `app/parsing/rules/rooms.py:22` и `app/parsing/rules/price.py:22` через
  неявную конкатенацию соседних строковых литералов — по образцу
  `app/parsing/rules/time.py`/`poi.py`.
- **3.2** Длинные URL-литералы в тестах
  (`tests/pik/test_url_builder.py:11,22,39`,
  `tests/integration/test_build_url_e2e.py:51`) — вынести в переменные с
  переносом через `+`, либо точечный `# noqa: E501`, если перенос ухудшает
  читаемость ассерта.
- **3.3** `app/ai/enrichment.py:230` — `except (ValueError, Exception) as e`
  → `except Exception as e` (`ValueError` — подкласс `Exception`, B014).
- После правок: `uv run ruff check` и `uv run ruff format --check` обязаны
  быть зелёными без новых `# noqa`, кроме явно обоснованных в 3.2.

### 3. Документация (AUDIT_REPORT.md, раздел 4)

- **4.1** Свести пять пересекающихся онбординг-документов к одному источнику
  правды. Кандидат — `ENABLE_ANTIGRAVITY.md` (на него уже ссылается
  `CLAUDE.md`). `ENABLE_AI.md`, `ANTIGRAVITY_INSTRUCTIONS.md`,
  `docs/db-setup.md` — удалить либо превратить в однострочную ссылку на
  основной документ. `SETUP.md` — оставить как общий чек-лист, синхронизировав
  раздел БД.
- **4.2** Поправить имена таблиц в `ENABLE_ANTIGRAVITY.md`/`docs/db-setup.md`:
  реальные имена — `ai_structured_facts`, `ai_semantic_cache` (не `ai_facts`/
  `district_facts`).
- **4.3** `SETUP.md:25` — заменить DSN-префикс `postgresql+asyncpg://` на
  `postgresql://` (`asyncpg` не понимает SQLAlchemy-стиль).
- **4.4** `app/knowledge_base/README.md:12` — поправить битую ссылку на
  `docs/ai-enrichment-architecture.md` (было `docs/ai_architecture_proposal.md`).
- **4.5** Актуализировать `README.md`: убрать/уточнить «никаких LLM в
  рантайме» **(если промпт 26 применён раньше — этот пункт уже закрыт там, не
  делать дважды)**; добавить `ai_used`/`ai_cache_hit`/`ai_explanation` в
  пример ответа; дополнить структуру каталогов (`app/ai/`, `app/geo/`,
  `app/knowledge_base/`).

### 4. Кандидаты на удаление (AUDIT_REPORT.md, раздел 5)

Перед удалением — повторно прогнать те же проверки, что и в отчёте (`grep -r`
по имени файла в `app/`, `tests/`, `*.md`, `.claude/skills/**`), содержимое
могло измениться с даты аудита. Если проверка подтверждает выводы отчёта:

- Удалить `.md`-артефакты прошлых сессий: `audit.md`, `center_report.md`,
  `explanation.md`, `report.md`, `recent_changes_last_2_hours.md`.
- Удалить root-level Python-скретчи: `check.py`, `refactor_entity_match.py`,
  `run_stress_test.py`, `scratch.py`, `test_overpass.py`, `test_regex.py`,
  `update_cache.py`, `update_json.py`. **Осторожно**: на дату этого промпта
  `scratch.py` содержит незакоммиченные изменения (ручной прогон сценария
  «трёшка ближайшая к МГУ» через `enrich()`) — перед удалением убедиться, что
  ничего ценного из этого прогона не потеряется (при необходимости перенести
  сценарий в `tests/integration/` как постоянный тест на промпт 23, а не
  просто выкинуть).
- Удалить `stress_test_output.txt` (застывший stdout-лог одного прогона).
- Проверить `pyproject.toml` — `google-antigravity>=0.1.7`: если пакет
  действительно не импортируется нигде (`grep -rn "import.*antigravity"
  app/`), а интеграция идёт только через внешний CLI `agy`
  (`app/ai/client.py`) — либо удалить зависимость, либо задокументировать в
  `CLAUDE.md`, почему она нужна (например, ставит сам бинарник `agy`).

## Требования к результату

- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check` зелёные.
- `git status` после удалений — только ожидаемые удаления/правки, ничего
  лишнего не затронуто.
- Никаких изменений поведения API (`POST /build-url`) — это чистка, не фича.

## Обнови CLAUDE.md

Короткая запись: что почищено (ссылка на разделы AUDIT_REPORT.md), где теперь
единственный источник правды по онбордингу ИИ/БД.

## Зависит от

Не зависит от 22–26 по коду, но пункты, помеченные «уже закрыто в N — не
делать дважды», нужно проверить по факту (какой промпт применили раньше) до
начала работы, чтобы не конфликтовать.
