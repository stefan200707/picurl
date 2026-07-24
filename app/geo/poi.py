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


class POIResult(BaseModel):
    count: int
    closest_distance_m: float | None = None


OVERPASS_URL = "https://overpass-api.de/api/interpreter"

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
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(mirror, data=query)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as e:
            last_error = e
            continue
    raise last_error if last_error else RuntimeError("нет доступных зеркал Overpass")


async def fetch_poi(lat: float, lon: float, category: POICategory, radius_m: int) -> POIResult:
    """Fetch POI from Overpass API (OSM)."""
    if category == POICategory.OTHER:
        return POIResult(count=0, closest_distance_m=None)

    query = get_overpass_query(lat, lon, category, radius_m)
    if not query:
        return POIResult(count=0, closest_distance_m=None)

    data = await _post_overpass(query)

    elements = data.get("elements", [])
    if not elements:
        return POIResult(count=0, closest_distance_m=None)

    # Calculate closest distance using haversine
    from app.geo.distance import haversine

    closest = None
    for el in elements:
        # nodes have lat/lon directly, ways/relations have center if we used `out center`
        el_lat = el.get("lat") or el.get("center", {}).get("lat")
        el_lon = el.get("lon") or el.get("center", {}).get("lon")

        if el_lat and el_lon:
            dist = haversine(lat, lon, el_lat, el_lon)
            if closest is None or dist < closest:
                closest = dist

    return POIResult(count=len(elements), closest_distance_m=closest)
