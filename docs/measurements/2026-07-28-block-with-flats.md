# Замер: эндпоинт `block-with-flats`, которым живёт выдача самого pik.ru

**Дата прогона:** 2026-07-28, ветка `main`.
**Код не менялся — это разведка, не правка.** Добавлен только разведочный
`scripts/block_with_flats_poc.py` (вне рантайма и вне `pytest`).

**Повод.** В DevTools на pik.ru видно, что карточки квартир тянет XHR
`block-with-flats?type=1,2&r…` (200, 311 kB), инициатор — бандл
`main-25647c751.js`. Рантаймовый `validate()` ходит в другое место —
`api.pik.ru/v2/filter`, который на `rooms=3` детерминированно отдаёт 500
(см. `2026-07-28-validator-500.md`). Гипотеза: `v2/filter` — посторонний
бэкенд, и раздел «Потолок pik.ru» в CLAUDE.md описывает не pik.ru.

## Итог одной строкой

Гипотеза подтверждена. Полный адрес —
`https://flat.pik-service.ru/api/v1/filter/block-with-flats`, он доступен с
нашего бэкенда **без кук, реферера и живой сессии**, и он **реально применяет
`metroStations`, `districtCounties`, `districtLocations`, `hasFinish`** — те
самые параметры, что `v2/filter` молча игнорировал. Единственное жёсткое
требование — User-Agent не должен начинаться на `curl/`.

## 1. Полный адрес: откуда получен

Фронт `www.pik.ru` закрыт Qrator (401 + JS-челлендж `/__qrator/qauth.js`),
cookie jar не помогает — 401 на повторе. Бандл достали в обход фронта:

1. Снапшот `www.pik.ru/search` из Wayback (`20260419183926`) — HTML отдаётся.
2. В нём ссылки на бандлы, лежащие на **отдельном CDN без Qrator**:
   `https://static.storage-cdn.ru/app/search/65cb1931/_next/static/chunks/…`.
3. Скачали `pages/_app-4dc133a7c61c7765.js` (9.8 MB после распаковки) — 200.

Сборка URL найдена в нём дословно:

```js
eF = await eT.pikFlatApi.fetch({
  method: "GET",
  uri: "/api/v1/filter/block-with-flats?".concat(eI)
})
```

Хост `pikFlatApi` — там же, в таблице сервисов:

```js
var WM = {
  pikApi: "https://api-selectel.pik-service.ru",
  pikFlatApi: "https://flat.pik-service.ru",
  pikFlatService: "https://flat.pik-service.ru/api",
  …
}
```

(Рядом лежит dev-конфиг с `pikFlatApi:"https://filter.dev-service.tech"` —
не наш случай, это сборка для dev-стенда.)

Итог: **`GET https://flat.pik-service.ru/api/v1/filter/block-with-flats?<query>`**.

### Что значит `type=1,2` из имени в DevTools

Реальное имя параметра — `types` (множественное; `type` эндпоинт не читает).
Это тип объекта: `1` = квартиры, `2` = апартаменты. То же поле приходит в
карточке как `typeId`, и оно же попадает в фасет `stats.types`.

### Сверка «тот ли это запрос»

Прямое доказательство — совпадение с числом на сайте. Сайт на
`pik.ru/search?rooms=3,2` показывал **3 673 квартиры и 43 проекта**:

| Query | `count` | `countBlocks` |
|---|---|---|
| `types=1,2&rooms=2,3` | 5073 | 65 |
| `types=1,2&rooms=2,3&location=2,3` | **3672** | **43** |

`countBlocks` совпал точно, `count` разошёлся на 1 из 3673 (за часы между
замерами данные живые). `location=2,3` = Москва и МО; **без него в выдачу
попадают регионы** (в items приезжали Улан-Удэ и Казань).

## 2. Доступность с нашего бэкенда

Живой сессии **не требуется**. Куки, `Referer`, `Origin` — не нужны.
Эндпоинт стоит не за Qrator, а за собственным nginx и отдаёт
`Access-Control-Allow-Origin: *`.

Единственное требование — User-Agent. При UA, начинающемся на `curl/`,
приходит **HTTP 200 и синтаксически валидный JSON, в котором фильтры не
применены** — то есть тихая нефильтрованная заглушка, отличимая только по
числам:

| User-Agent | `count` при `types=1,2&rooms=3` |
|---|---|
| `curl/8.7.1` | 24088 ← заглушка, фильтр не применён |
| `python-httpx/0.27.0` | **1326** |
| `picurl/1.0` | **1326** |
| `Mozilla/5.0` | **1326** |
| `Mozilla/5.0 (Macintosh; …) Chrome/126…` | **1326** |

То есть отсекается буквально подстрока `curl`, а не «неизвестный клиент»:
`picurl/1.0` проходит. Свой честный UA ставить можно.

**Это самая опасная находка замера.** Заглушка не отличается ни кодом
ответа, ни структурой — только величиной `count` и составом `data.time`
(у заглушки `{"filter":…, "stats":…}`, у настоящего ответа появляется
`filter_block`). Клиент без правильного UA будет молча получать
максимальный `count` и считать, что «нашлось много».

### Нестабильность: наблюдалась, причина не установлена

В окне ~12:40–12:45 на 12 одинаковых запросах с браузерным UA пришло
**9 корректных (1326) и 3 заглушки (24088)**; в другой серии — 6 из 10 и
4 из 10. Уникальный cache-buster (`&_=<rand>`) в тот момент давал 10/10
корректных, `Cache-Control: no-cache` не помогал (2 заглушки из 10).

Позже (~13:10) нестабильность **перестала воспроизводиться**: 12/12 и 22/22
корректных подряд даже без cache-buster.

Проверенная и **опровергнутая** гипотеза — что кэш травят наши же запросы с
`curl`-UA. На свежем, никем не тронутом кэш-ключе:

```
5 браузерных запросов:                1012 1012 1012 1012 1012
1 запрос тем же ключом с UA=curl/8.7.1: 24088
снова 5 браузерных, ключ тот же:      1012 1012 1012 1012 1012
```

Заглушка на общий ключ не садится. Причина расхождения остаётся
**неустановленной** (правдоподобно — часть узлов за балансировщиком держала
устаревший объект). Практический вывод: cache-buster стоит ставить всегда, а
на согласованность полагаться нельзя без повторного замера.

## 3. Что возвращает

`{"success": bool, "data": {"cache", "time", "stats", "items"}}`.
Общий счётчик есть — **`data.stats.count`** (квартир) и
**`data.stats.countBlocks`** (ЖК). Это не только счётчик: 311 kB — карточки.

`items` — **список ЖК**, карточки квартир вложены в `flats` каждого ЖК
(при `onlyFlats=1`). Поля ЖК: `id`, `guid`, `name`, `path`, `latitude`,
`longitude`, `priceMin`, `regionId`, `image`, `roomsStatistics`
(`{"1": {"priceMin", "totalFlat"}, …}`), `timeOnFoot`, `timeOnTransport`.

Поля квартиры — всё, что просили, есть: `id`, `guid`, `area`, `floor`,
`maxFloor`, `price`, `meterPrice`, `rooms`, `status`, `typeId`,
`settlementDate` (полная дата, не год), `finishType`, `blockName`,
`blockSlug`, `bulkName`, `planUrl`, `metro`, `options`, `labels`, `tags`,
`currentBenefitId`.

Сверх счётчика `stats` отдаёт **min/max по осям** (`priceMin/Max`,
`areaMin/Max`, `areaKitchenMin/Max`, `floorMin/Max`, `timeOnFootMin/Max`,
`timeOnTransportMin/Max`) и **фасеты — списки значений, доступных при текущем
фильтре**: `blocks`, `bulks`, `locations`, `finishTypes`, `rooms`,
`settlements`, `benefits`, `option`, `optionGroup`, `requiredTags`,
`parkingTypes`, `attributes` и `district.{metroStations, districtLocations,
districtCounties}`. Пагинация — `currentPage` / `lastPage`.

## 4. Какие параметры реально фильтруют

Базис — `types=1,2&rooms=3` (Москва+МО не задана), `count=1326`,
`countBlocks=58`. Каждый прогон с cache-buster и браузерным UA.

| Параметр | `count` | Вывод |
|---|---|---|
| baseline | 1326 | — |
| `metroStations=1ee78b51-f611-…` | **42** | **фильтрует** |
| `metroStations=deadbeef-0000-…` | 1326 | невалидный GUID молча игнорируется |
| `districtCounties=2` | **52** | **фильтрует** |
| `districtCounties=999999` | `success:false`, `FATAL_ERROR` | невалидный id — явная ошибка |
| `districtLocations=196` | **34** | **фильтрует** |
| `blocks=477` | **15** | фильтрует |
| `blocks=999999` | **0** | фильтрует |
| `hasFinish=1` / `hasFinish=0` | **879** / **138** | **фильтрует** |
| `priceTo=15000000` | **198** | фильтрует |
| `areaFrom=100` | **14** | фильтрует |
| `floorFrom=20` | **217** | фильтрует |
| `ready=1` | **69** | фильтрует |
| `status=free` | **978** | фильтрует |
| `location=2,3` / `location=2` | **1012** / **760** | фильтрует (регион) |
| `finish=1`, `finishType=1`, `finishTypes=1` | 1326 | не читаются — имя `hasFinish` |
| `settlement=2027-01-31`, `…T00:00:00+00:00`, `settlementTo=`, `settlementYearTo=` | 1326 | **имя не подобрано** |
| `type=1,2`, `locations=`, `flatLimit=`, `flatPage=` | без эффекта | имена — `types`, `location` |

**Три из трёх параметров, которые `v2/filter` игнорировал** (`metroStations`,
`districtCounties`, `districtLocations` — константа `UNVERIFIED_LOCATION_PARAMS`
в `app/pik/validator.py`), здесь работают. Отделка тоже, но под именем
`hasFinish` — булевым, а не перечислением, как наш `finish: list[Finish]`.

Не найдено имя для **срока сдачи**. В бандле поле называется `settlement` и
берёт значение из фасета `stats.settlements` (даты ISO), но ни одна из
опробованных форм не сузила выдачу. Остаётся открытым вопросом.

Схема фильтров из бандла (совпадает с нашим `to_query_dict()` почти
один-в-один): `areaLivingTo, areaKitchenFrom, areaKitchenTo, floorFrom,
floorTo, settlement, status, notFirstFloor, notLastFloor, lastFloor,
hasFinish, rooms, metroStations, districtCounties, districtLocations,
attribute, parkingType, timeOnFoot, optionGroups`.

### Побочная находка: наши id округов эндпоинт не признаёт

Базис — Москва+МО, все комнатности, `count=8209`:

| Наш id (`counties.json`) | `count` |
|---|---|
| метро «Аминьевская» (GUID) | 130 |
| метро «Аннино» (GUID) | 2 |
| метро «Аэропорт Внуково» (GUID) | 207 |
| метро «Багратионовская» (GUID) | 1 |
| метро «Ботанический сад» (GUID) | 27 |
| метро «Братиславская» (GUID) | 433 |
| `districtCounties=33` (ВАО) | **0** |
| `districtCounties=34` (ЗАО) | **0** |
| `districtCounties=35` (Новомосковский АО) | **0** |
| `districtCounties=59` (САО) | **0** |

Все проверенные GUID метро валидны и сужают. А **все проверенные id округов
дают ноль** — при том, что `districtCounties=2` даёт 52, а фасет возвращает
`[2, 6, 9, 75, 88, 133, 134, 135, 136, 137, 514]`, где чисел 33/34/35/59 нет.
Похоже, что `counties.json` содержит id из другой номенклатуры. Проверить это
раньше было нечем: `v2/filter` игнорировал `districtCounties` целиком, поэтому
дефект (если он есть) был невидим. **Отдельная задача, за рамками этой разведки.**

## 5. Признаки стабильности

| Признак | Наблюдение |
|---|---|
| Версия в пути | Есть — `/api/v1/`. `/api/v2/filter/block-with-flats` → 404 |
| Соседние маршруты | `/api/v1/filter/block` → 200 (528 kB), `/api/v1/filter/flat` → 200 (59 kB), `/api/v1/filter/stats` → 404 |
| Методы | Только GET. `POST` и `OPTIONS` → 404 |
| CORS | `Access-Control-Allow-Origin: *`, `Allow-Headers: *` — рассчитан на вызов из чужого браузера |
| Индексация | `X-Robots-Tag: noindex, nofollow, nosnippet, noarchive` |
| Защита | Qrator отсутствует (в отличие от `www.pik.ru`); только фильтр по UA |
| Служебные поля в теле | `data.cache`, `data.time` с таймингами внутренних стадий — признак внутреннего, не публичного контракта |

**Оценка связанности.** Публичного контракта нет: ни версии в заголовках, ни
документации, служебные тайминги наружу — это внутренний API фронта. Против
этого — два измеренных факта: (а) бандл из снапшота **19 апреля 2026** ссылается
на **тот же самый URL**, который работает 28 июля 2026 — три с лишним месяца
без смены пути; (б) имена параметров совпадают с теми, что проект уже
использует для `v2/filter`, то есть модель фильтров у ПИК общая, и менять её
дорого им самим.

Риск связанности всё равно **выше**, чем у `v2/filter`: тот хотя бы не менялся
между 27.07 и 28.07 и не отличает браузер от робота. Здесь же добавляется
поведенческая зависимость от UA-эвристики, которую могут ужесточить в любой
момент — и она деградирует **тихо**, отдавая 200 с неправильными числами.

## Вывод для проекта

1. Раздел «Потолок pik.ru» в CLAUDE.md описывает ограничения `api.pik.ru/v2/filter`,
   а не pik.ru. Утверждение «бэкенд игнорирует `metroStations`/`districtLocations`/
   `districtCounties`» **верно только для `v2/filter`**.
2. На `flat.pik-service.ru` `result_count` при локационном фильтре **что-то
   доказывает** — в отличие от `v2/filter`.
3. Появляется способ проверить 183 локационных id (сейчас живыми данными
   подтверждены 72, все — ЖК). Первая же выборка выявила расхождение по округам.
4. Любой переход на этот эндпоинт обязан: ставить не-`curl` UA, ставить
   cache-buster, задавать `location`, и **отличать заглушку от ответа**
   (например, по наличию `data.time.filter_block`) — иначе тихо завышенный
   `result_count`.

## Как воспроизвести

```bash
# основной прогон
uv run python scripts/block_with_flats_poc.py --rooms 3 --price-to 20000000

# ловушка 1: тихая заглушка при curl-UA (count=24088 вместо 1012)
uv run python scripts/block_with_flats_poc.py --ua curl

# ловушка 2: прогон без cache-buster
uv run python scripts/block_with_flats_poc.py --no-cache-buster --repeat 12

# сверка с сайтом: должно быть countBlocks=43
curl -sS -A 'Mozilla/5.0' \
  'https://flat.pik-service.ru/api/v1/filter/block-with-flats?types=1,2&rooms=2,3&location=2,3&_=1' \
  | python3 -c 'import json,sys; s=json.load(sys.stdin)["data"]["stats"]; print(s["count"], s["countBlocks"])'
```
