# 07 — URL-builder: Criteria -> URL — Milestone 4

> Прочитай `_conventions.md` и `docs/pik-url-schema.md` перед началом.

## Контекст

Ядро сервиса: превратить `Criteria` в корректный URL `pik.ru/search`. Главная
сложность — закономерность **single-путь / multi-query**.

## Цель

`app/pik/url_builder.py` — функция `build_url(criteria: Criteria) -> str`.

## Правила построения

### База
`https://www.pik.ru/search`

### Путь (порядок сегментов: комнатность → отделка/заселение → локация)
- Комнатность: **ровно одна** → слаг в путь (`two-room`); **несколько** → query
  `rooms=-1,1,2` (студия = `-1`), сегмента в пути нет.
- Отделка «Готовая» → сегмент `finish`; «Заселение сразу» → `ready`.
  (Уточни в схеме, могут ли сосуществовать; по данным — отдельные сегменты.)
- Локация: **ровно один округ** → слаг (`zao`); **ровно одно метро** →
  `m-<слаг>`. При **нескольких** — путь исчезает, идёт query
  (`districtCounties=...`, `metroStations=<GUID>,...`).
- Районы — **всегда** query (`districtLocations=id,...`), single-в-путь нет.
- ЖК — single-путь только на страницах ЖК; в нашем сценарии ЖК идёт как доп.
  фильтр → query `blocks=id,...`.

### Приоритет пути при конфликте
Если заданы одновременно и метро, и округ, и т.п. — определи детерминированный
порядок сегментов пути и что уходит в query. Задокументируй правило в docstring
(разумно: комнатность в путь всегда, локация в путь только если ровно одна
локационная сущность всего).

### Query-параметры
Маппинг всех полей `Criteria` в параметры по таблице из `docs/pik-url-schema.md`:
`priceFrom/priceTo`, `areaFrom/areaTo`, `areaKitchenFrom/To`, `floorFrom/To`,
`notFirstFloor=1`, `lastFloor=1`, `timeOnFoot`, `timeOnTransport`,
`sortBy`+`orderBy`, `settlementYearFrom/To`, `settlementMonthFrom/To`,
`currentBenefit`, `optionGroups` (comma-list), `options`, `type=1`, `status=free`,
`blocks`, `districtLocations`, `districtCounties`, `metroStations`, `rooms`.

### Детерминизм и чистота
- **Стабильный порядок** query-параметров (важно для тестов и кэша): фиксированный
  порядок ключей, значения-списки — через запятую в заданном порядке.
- Пустые/None-поля не добавляются.
- Корректная URL-кодировка (но слаги уже url-safe).
- Никакой сети.

## Требования к результату

- Юнит-тесты `tests/pik/test_url_builder.py`:
  - **Эталон из ТЗ**:
    `two-room` + priceTo=15000000 + метро «Аэропорт Внуково» + finish + price_asc
    → `https://www.pik.ru/search/two-room/finish/m-aeroport-vnukovo?priceFrom=0&priceTo=15000000&sortBy=price&orderBy=asc`
    (сверься с точным ожидаемым URL из примера ответа ТЗ; согласуй, добавляем ли
    `priceFrom=0` при заданном только `priceTo` — зафиксируй решение).
  - **Живой эталон**: 2-комн., 10–15 млн, м. Аэропорт Внуково, area desc →
    `.../search/two-room/m-aeroport-vnukovo?priceFrom=10000000&priceTo=15000000&sortBy=area&orderBy=desc`.
  - single → путь; 2+ той же категории → query (комнатность, округа, метро).
  - районы всегда в query; ЖК как `blocks`.
  - тип/статус/этаж/площадь/год сдачи.

## Обнови CLAUDE.md

Кратко: `build_url` — ядро; правило single-путь/multi-query; порядок сегментов.

## Зависит от

01, 02.
