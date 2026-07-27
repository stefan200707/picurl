from enum import StrEnum

import httpx
from pydantic import BaseModel


class POICategory(StrEnum):
    SCHOOL = "school"
    KINDERGARTEN = "kindergarten"
    SHOP = "shop"
    PARKING = "parking"
    PARK_FOREST = "park_forest"
    MEDICAL = "medical"
    OTHER = "other"


#: Версия схемы записи POI-кэша. v1 (без этого поля) считал
#: ``closest_distance_m`` по ВСЕМ элементам ответа Overpass, включая
#: стройплощадки, — использовать её как «дистанцию до действующего объекта»
#: нельзя (см. POI_CACHE_STALE_HINT и разбор в docstring :func:`is_operational`).
POI_CACHE_SCHEMA_VERSION = 2

#: Что сказать пользователю, если в кэше найдена запись старой схемы.
POI_CACHE_STALE_HINT = (
    "POI-кэш устаревшей схемы (v1): дистанция могла считаться по строящимся "
    "объектам — пересоберите: uv run python -m app.geo.refresh_poi"
)

#: Ключи-префиксы OSM, помечающие объект как ЕЩЁ НЕ работающий: сама стройка
#: (``construction:amenity=kindergarten``), проектируемое и предложенное.
_NON_OPERATIONAL_PREFIXES = ("construction:", "planned:", "proposed:")

#: Голые ключи того же смысла. ``construction=education`` + ``landuse=construction``
#: — ровно та комбинация, которой огорожена стройплощадка relation/13512774,
#: приходившая в кэш как «детский сад в 186 м».
_NON_OPERATIONAL_KEYS = ("construction", "planned", "proposed")


def is_operational(tags: dict[str, str]) -> bool:
    """Работает ли объект уже сейчас (а не строится/проектируется).

    Граница осознанно узкая — врёт только НЕРАБОЧИЙ объект:

    * ``construction``/``planned``/``proposed`` (голым ключом или префиксом),
      а также ``landuse=construction`` → объекта фактически нет, на карте это
      огороженный забором котлован. Из дистанции исключаем.
    * Дошкольные корпуса школьных комплексов («Школа № 1576. Корпус № 16») —
      НАСТОЯЩИЕ работающие детсады московской системы образования, просто
      подписаны как школы. Не исключаем и не понижаем.
    * Безымянные объекты и частные/встроенные сети (``ownership=private``,
      «Sun School») — реальны, остаются; их особенности видны в полях
      ``closest_unnamed``/``closest_name``, чтобы пользователь понимал, ПОЧЕМУ
      ЖК прошёл, а не смотрел на голое число метров.
    """
    if tags.get("landuse") == "construction":
        return False
    for key in tags:
        if key in _NON_OPERATIONAL_KEYS:
            return False
        if key.startswith(_NON_OPERATIONAL_PREFIXES):
            return False
    return True


class POIResult(BaseModel):
    """Запись POI-кэша по одному ЖК и одной категории.

    Ключевое правило: ``closest_distance_m`` — дистанция до ближайшего
    ДЕЙСТВУЮЩЕГО объекта, и именно она работает отсечкой пользовательского
    ``max_distance_m``. Строящиеся объекты не отбрасываются молча (инвариант 1),
    а живут отдельными полями.
    """

    #: Всего объектов в радиусе сбора (действующие + строящиеся) — историческое
    #: поле, сохранено ради читаемости диффов и обратной совместимости.
    count: int
    #: Дистанция до ближайшего ДЕЙСТВУЮЩЕГО объекта (метры).
    closest_distance_m: float | None = None
    #: Имя ближайшего действующего объекта; ``None`` — он безымянный в OSM.
    closest_name: str | None = None
    #: Ближайший действующий объект без ``name`` (на карте — просто двор).
    closest_unnamed: bool = False
    #: Сколько объектов реально работает.
    count_operational: int = 0
    #: Сколько отброшено как строящиеся/проектируемые.
    count_under_construction: int = 0
    #: Дистанция до ближайшей стройки — факт сохраняется, но фильтром не служит.
    closest_under_construction_m: float | None = None
    #: 1 — запись собрана до разделения на действующие/строящиеся.
    schema_version: int = 1


OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: Таймаут HTTP-запроса к зеркалу (сек). Замер 2026-07 на рабочем зеркале
#: maps.mail.ru: один запрос «сады в радиусе 2 км» отвечает ~22 с, то есть
#: прежние 30 с под нагрузкой рвались по ReadTimeout ещё до ответа — сбор кэша
#: срывался «сетевой ошибкой» там, где сервер просто думал.
OVERPASS_TIMEOUT_S = 120.0

# Зеркала Overpass в порядке приоритета. Основной хост регулярно недоступен
# (429/504 под нагрузкой, а из части сетей — вообще не резолвится), и это
# единственная причина, по которой poi_cache.json годами оставался пустым:
# при отказе одного хоста наполнение кэша срывалось целиком. Перебор зеркал
# делает офлайн-сбор устойчивым.
#
# ВАЖНО: в список входят только зеркала с ПОЛНОЙ планетой. Региональные
# (например overpass.osm.ch — только Швейцария) включать нельзя: на московские
# координаты они честно отвечают HTTP 200 и count=0 — молчаливая ложь вместо
# явного отказа. Проверено живым запросом 2026-07-23: osm.ch для Цюриха даёт
# 57 садиков, для Саларьево — 0; maps.mail.ru для Саларьево — 10.
OVERPASS_MIRRORS: tuple[str, ...] = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
)


def get_overpass_query(lat: float, lon: float, category: POICategory, radius_m: int) -> str:
    tags = {
        POICategory.SCHOOL: [
            '"amenity"="school"',
            '"construction:amenity"="school"',
            '"planned:amenity"="school"',
            '"proposed:amenity"="school"',
        ],
        POICategory.KINDERGARTEN: [
            '"amenity"="kindergarten"',
            '"construction:amenity"="kindergarten"',
            '"planned:amenity"="kindergarten"',
            '"proposed:amenity"="kindergarten"',
        ],
        POICategory.SHOP: ['"shop"~"supermarket|convenience"'],
        POICategory.PARKING: ['"amenity"="parking"'],
        # поликлиника/клиника=clinic/doctors, больница/роддом=hospital, аптека=pharmacy
        POICategory.MEDICAL: ['"amenity"~"clinic|hospital|doctors|pharmacy"'],
    }

    if category == POICategory.PARK_FOREST:
        tag_query = (
            f'nwr["leisure"="park"](around:{radius_m},{lat},{lon});\n'
            f'nwr["natural"="wood"](around:{radius_m},{lat},{lon});\n'
            f'nwr["landuse"="forest"](around:{radius_m},{lat},{lon});'
        )
    elif category in tags:
        queries = [f"nwr[{tag}](around:{radius_m},{lat},{lon});" for tag in tags[category]]
        tag_query = "\n".join(queries)
    else:
        return ""

    return f"""[out:json][timeout:25];
(
  {tag_query}
);
out center;"""


async def _post_overpass(query: str) -> dict:
    """Выполнить запрос к Overpass, перебирая зеркала до первого успеха.

    Сетевые ошибки и временные отказы (429/5xx) одного зеркала не фатальны —
    пробуем следующее. Если не ответило ни одно, поднимаем последнюю ошибку:
    вызывающий код (refresh_poi) обязан увидеть отказ, а не пустой результат.
    """
    last_error: Exception | None = None
    for mirror in OVERPASS_MIRRORS:
        try:
            async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT_S) as client:
                response = await client.post(mirror, content=query.encode("utf-8"))
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as e:
            last_error = e
            continue
    raise last_error if last_error else RuntimeError("нет доступных зеркал Overpass")


def _empty_result() -> POIResult:
    return POIResult(count=0, closest_distance_m=None, schema_version=POI_CACHE_SCHEMA_VERSION)


async def fetch_poi(lat: float, lon: float, category: POICategory, radius_m: int) -> POIResult:
    """Собрать POI категории вокруг точки, разделяя действующие и строящиеся.

    Дистанция считается ТОЛЬКО по действующим объектам (:func:`is_operational`) —
    иначе требование «садик в 200 метрах» проходит по стройплощадке, что и
    произошло в живом прогоне с ЖК «Нарвин». Строящиеся не исчезают: их число и
    дистанция сохраняются отдельными полями (инвариант 1).
    """
    if category == POICategory.OTHER:
        return _empty_result()

    query = get_overpass_query(lat, lon, category, radius_m)
    if not query:
        return _empty_result()

    data = await _post_overpass(query)

    elements = data.get("elements", [])
    if not elements:
        return _empty_result()

    # Calculate closest distance using haversine
    from app.geo.distance import haversine

    operational: list[tuple[float, str | None]] = []
    under_construction: list[float] = []
    for el in elements:
        # nodes have lat/lon directly, ways/relations have center if we used `out center`
        el_lat = el.get("lat") or el.get("center", {}).get("lat")
        el_lon = el.get("lon") or el.get("center", {}).get("lon")
        if not el_lat or not el_lon:
            continue

        dist = haversine(lat, lon, el_lat, el_lon)
        tags = el.get("tags") or {}
        if is_operational(tags):
            operational.append((dist, tags.get("name")))
        else:
            under_construction.append(dist)

    operational.sort(key=lambda pair: pair[0])
    closest_distance, closest_name = operational[0] if operational else (None, None)

    return POIResult(
        count=len(elements),
        closest_distance_m=closest_distance,
        closest_name=closest_name,
        closest_unnamed=bool(operational) and closest_name is None,
        count_operational=len(operational),
        count_under_construction=len(under_construction),
        closest_under_construction_m=min(under_construction, default=None),
        schema_version=POI_CACHE_SCHEMA_VERSION,
    )
