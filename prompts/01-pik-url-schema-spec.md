# 01 — Спецификация URL-схемы pik.ru (документ в репо) — Milestone 0

> Прочитай `_conventions.md` перед началом.

## Контекст

URL-схема `pik.ru/search` восстановлена вживую (кликами по фильтрам, фиксируя
адресную строку). Документации у сайта нет. Эти знания нужно **зафиксировать в
репозитории как единый источник правды** — на него будут опираться промпты
03 (справочники), 07 (url_builder) и 08 (validator).

## Цель

Файл `docs/pik-url-schema.md` — исчерпывающая, машинно-читаемая человеком
спецификация схемы фильтров. Это документ, **не код**.

## Ключевая закономерность (главное!)

Подтверждена на трёх независимых фильтрах (комнатность, округ, метро):

- **Единственное** выбранное значение → уходит в **путь** человекочитаемым слагом.
- **Два и больше** значений → путь пропадает, появляется **query-параметр** со
  списком «сырых» id/GUID через запятую.

Это правило `single-путь / multi-query` — центральное для url_builder.

## Что описать в docs/pik-url-schema.md

### Сегменты пути (порядок: `{комнатность}/{отделка-или-заселение}/{локация}`, каждый опционален)

| Фильтр | Значения | Пример |
|--------|----------|--------|
| Комнатность | `studio`, `one-room`, `two-room`, `three-room` (чип «3+» → `three-room`) | `/search/two-room` |
| Отделка «Готовая» | `finish` | `/search/two-room/finish` |
| «Заселение сразу» | `ready` | `/search/two-room/ready` |
| Округ (Москва) | код округа, напр. `zao` | `/search/three-room/zao` |
| Метро | `m-<слаг-станции>` | `/search/two-room/m-aeroport-vnukovo` |

### Single (путь) vs Multi (query)

| Фильтр | Single (путь) | Multi (query) |
|--------|---------------|---------------|
| Комнатность | `/search/two-room` | `?rooms=-1,1,2` (студия = `-1`) |
| Округ | `/search/two-room/zao` | `?districtCounties=9,6` |
| Метро | `/search/two-room/m-aeroport-vnukovo` | `?metroStations=<GUID>,<GUID>` |
| Район | (нет single-в-путь) — всегда query | `?districtLocations=520,203` |
| ЖК | `/search/{жк-слаг}/two-room` (только со страницы ЖК) | `?blocks=1108,...` (как доп. фильтр) |

### Query-параметры (все подтверждены живыми кликами)

| Параметр | Смысл | Пример |
|----------|-------|--------|
| `priceFrom` / `priceTo` | цена, руб | `priceFrom=10000000&priceTo=15000000` |
| `areaFrom` / `areaTo` | площадь общая, м² | `areaFrom=70&areaTo=100` |
| `areaKitchenFrom` / `areaKitchenTo` | площадь кухни, м² | `areaKitchenFrom=8&areaKitchenTo=12` |
| `floorFrom` / `floorTo` | этаж (диапазон) | `floorFrom=5&floorTo=20` |
| `notFirstFloor=1` | чекбокс «Не первый» | |
| `lastFloor=1` | чекбокс «Последний» | |
| `timeOnFoot` | время до метро пешком, мин | `timeOnFoot=15` |
| `timeOnTransport` | время до метро на транспорте, мин | `timeOnTransport=15` |
| `sortBy` + `orderBy` | `sortBy=price\|area`, `orderBy=asc\|desc` | `sortBy=price&orderBy=asc` |
| `blocks` | конкретный ЖК, числовой id | `blocks=1108` |
| `districtLocations` | конкретный район (не округ!), id(-ы) | `districtLocations=203` |
| `settlementYearFrom` / `settlementYearTo` | год сдачи | `settlementYearFrom=2027` |
| `settlementMonthFrom` / `settlementMonthTo` | месяц сдачи (1–12) | `settlementMonthFrom=3&settlementMonthTo=8` |
| `currentBenefit` | программа покупки, слаг | `currentBenefit=rassrochka-na-12-mesyacev-7` |
| `optionGroups` | «Особенности планировки», comma-list слагов | `optionGroups=smartHomePIK,balcony` |
| `options` | «Вид из окна», слаг | `options=vidNaVodu` |
| `type` | `1` = только квартиры (без апартаментов); отсутствует = оба | `type=1` |
| `status` | `free` = «Не показывать забронированные» | `status=free` |

### Полный пример (эталон для теста url_builder)

2-комн., 10–15 млн, м. Аэропорт Внуково, сортировка по убыванию площади:

```
https://www.pik.ru/search/two-room/m-aeroport-vnukovo?priceFrom=10000000&priceTo=15000000&sortBy=area&orderBy=desc
```

### Маркер пустой выдачи (для отладки)

На HTML-странице: «По заданным параметрам не удалось ничего подобрать» /
«Попробуйте изменить настройки фильтров». Валидацию всё равно делаем через JSON API
(SPA), но маркер полезен как fallback-проверка.

## Дополнительно зафиксировать

- **Открытый вопрос №1** (дефолтная сортировка) — записать выбранное решение.
- Раздел «Сознательно не проверено» (не блокер, собрать на шаге 03): полный список
  слагов `optionGroups/options/currentBenefit`, «Тип паркинга», поведение 2+ ЖК.

## Требования к результату

- `docs/pik-url-schema.md` содержит все таблицы выше в аккуратном виде.
- Явно выделена закономерность single-путь/multi-query.
- Отмечено, какие поля точно подтверждены, а какие — предположения.

## Обнови CLAUDE.md

Ссылка на `docs/pik-url-schema.md` как на источник правды по URL-схеме.

## Зависит от

00.
