# 02 — Модель Criteria и модели API (pydantic v2) — основа Milestone 3

> Прочитай `_conventions.md` перед началом.

## Контекст

Все механизмы (парсинг структурных фактов, матчинг сущностей, url_builder,
эндпоинт) сходятся к **единой pydantic-модели `Criteria`**. Её надо спроектировать
рано, чтобы остальные промпты опирались на стабильный контракт.

## Цель

Файл `app/parsing/schema.py` с моделью `Criteria` и вспомогательными enum/типами,
плюс модели запроса/ответа API (можно тут же или в `app/main.py` — реши и
задокументируй).

## Что заложить в Criteria

Модель должна покрывать всё, что url_builder умеет превращать в URL (см.
`docs/pik-url-schema.md`). Поля — опциональные (None = не задано):

- **rooms**: список/множество из `{studio, one, two, three_plus}` (маппинг в
  слаги/`rooms=-1,1,2` — задача url_builder, не схемы). Продумай single vs multi.
- **price_min / price_max**: `int | None`, рубли.
- **area_min / area_max**, **area_kitchen_min / area_kitchen_max**: `float | None`.
- **floor_min / floor_max**: `int | None`; **not_first_floor**, **last_floor**: `bool`.
- **finish**: `bool | None` («с отделкой»/«без отделки»/«черновая» — продумай,
  нужен ли трёхзначный вариант или отдельный enum отделки).
- **ready**: `bool | None` («заселение сразу»).
- **metro**: список записей метро (человекочитаемое имя + слаг + id/GUID — но в
  `Criteria` храни то, что вернул матчер; финальный маппинг у url_builder).
- **counties** (округа), **districts** (районы), **complexes** (ЖК): аналогично.
- **time_on_foot / time_on_transport**: `int | None`, минуты.
- **settlement_year_from/to**, **settlement_month_from/to**: `int | None`.
- **sort**: enum/структура `{field: price|area, order: asc|desc}` или строковый
  ключ вида `price_asc` (см. пример ответа в ТЗ: `"sort": "price_asc"`).
- **housing_type**: `flats_only` / `any` (маппинг в `type=1`).
- **only_available**: `bool` (маппинг в `status=free`).
- прочие: `currentBenefit`, `optionGroups`, `options`, `view` — можно заложить
  как опциональные строки/списки слагов для расширяемости.

## Модели API

- **BuildUrlRequest**: `{ text: str }`.
- **BuildUrlResponse**: `{ url: str, criteria: <публичное представление>,
  result_count: int | None, warnings: list[str] }` — как в примере ТЗ.
- Публичное представление `criteria` в ответе должно быть человекочитаемым
  (напр. `{"rooms": "2", "price_max": 15000000, "metro": ["Аэропорт Внуково"],
  "finish": true, "sort": "price_asc"}`), а не сырым внутренним объектом. Продумай
  сериализацию (можно отдельная `CriteriaView` или `model_dump` с алиасами).

## Требования к результату

- pydantic v2, без `RootModel`, без `...`-эллипсиса в полях (skill `fastapi`).
- Значения по умолчанию делают «пустой» `Criteria()` валидным.
- Юнит-тесты `tests/parsing/test_schema.py`: конструирование, сериализация,
  соответствие публичного вида примеру из ТЗ.

## Обнови CLAUDE.md

Кратко: где живёт контракт `Criteria` и модели API.

## Зависит от

00, 01.
