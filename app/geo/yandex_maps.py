"""Модуль интеграции с Яндекс.Картами и построения маршрутов.

Обеспечивает:
1. Автоматическую загрузку и конфигурацию Яндекс.Карт для фронтенда и API.
2. Задание двух точек:
   - Точка А (origin): жилой комплекс (ЖК) ПИК с реальными координатами.
   - Точка Б (destination): целевая точка (метро, вуз/МГУ, ориентир, парк, POI или центр).
3. Построение маршрута между двумя точками с поддержкой способов передвижения
   (пешком, на машине, на общественном транспорте, на велосипеде).
4. Расчёт времени в пути от ЖК до целевого места и выбор/фильтрацию ЖК
   в зависимости от запрошенного пользователем времени (например, «до 15 минут пешком»).
"""

from __future__ import annotations

import logging
import math
from enum import StrEnum

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings
from app.geo.distance import haversine
from app.parsing.schema import Criteria
from app.reference.loader import RefEntry, find_by_name, load_all, load_metro

logger = logging.getLogger(__name__)

# Средние скорости передвижения по Москве (м/мин) и коэффициенты извилистости дорог
SPEEDS_METERS_PER_MIN: dict[str, float] = {
    "pedestrian": 83.33,  # ~5 км/ч шагом
    "driving": 500.0,  # ~30 км/ч на авто с учётом городского трафика
    "transit": 333.33,  # ~20 км/ч на городском транспорте/метро
    "bicycle": 250.0,  # ~15 км/ч на велосипеде/самокате
}

# Коэффициенты удлинения пути по улично-дорожной сети относительно прямой дуги (haversine)
WINDING_FACTORS: dict[str, float] = {
    "pedestrian": 1.25,
    "driving": 1.35,
    "transit": 1.30,
    "bicycle": 1.20,
}

# Добавочное время (минуты) на ожидание/пересадку для общественного транспорта
TRANSIT_FIXED_OVERHEAD_MIN: int = 3


class TravelMode(StrEnum):
    """Способ передвижения по маршруту."""

    PEDESTRIAN = "pedestrian"  # пешком
    DRIVING = "driving"  # на автомобиле / такси
    TRANSIT = "transit"  # общественный транспорт
    BICYCLE = "bicycle"  # велосипед / самокат

    @property
    def label_ru(self) -> str:
        labels = {
            TravelMode.PEDESTRIAN: "пешком",
            TravelMode.DRIVING: "на машине",
            TravelMode.TRANSIT: "на транспорте",
            TravelMode.BICYCLE: "на велосипеде",
        }
        return labels.get(self, "в пути")

    @property
    def yandex_rtt(self) -> str:
        """Код типа маршрутизации для URL Яндекс.Карт."""
        rtt_map = {
            TravelMode.PEDESTRIAN: "pd",
            TravelMode.DRIVING: "auto",
            TravelMode.TRANSIT: "mt",
            TravelMode.BICYCLE: "bc",
        }
        return rtt_map.get(self, "pd")


class GeoPoint(BaseModel):
    """Географическая точка (Точка А или Точка Б)."""

    model_config = ConfigDict(extra="ignore")

    lat: float = Field(description="Широта")
    lon: float = Field(description="Долгота")
    name: str | None = Field(default=None, description="Название точки (ЖК, метро, вуз и т.д.)")
    address: str | None = Field(default=None, description="Адрес или описание локации")
    point_type: str = Field(
        default="origin",
        description="Тип точки: origin (Точка А / ЖК) или destination (Точка Б / назначение)",
    )


class RouteLeg(BaseModel):
    """Сводка построенного маршрута между двумя точками."""

    model_config = ConfigDict(extra="ignore")

    origin: GeoPoint = Field(description="Точка А (старт / ЖК)")
    destination: GeoPoint = Field(description="Точка Б (финиш / целевое место)")
    travel_mode: TravelMode = Field(default=TravelMode.PEDESTRIAN, description="Способ перемещения")
    distance_m: float = Field(description="Длина маршрута в метрах")
    duration_min: int = Field(description="Время в пути в минутах")
    duration_sec: int = Field(description="Время в пути в секундах")
    formatted_duration: str = Field(description="Человекочитаемое время, напр. '12 мин пешком'")
    formatted_distance: str = Field(description="Человекочитаемая дистанция, напр. '1.2 км'")
    waypoints: list[list[float]] = Field(
        default_factory=list,
        description="Координаты точек полилинии маршрута [[lat, lon], ...]",
    )
    yandex_maps_url: str = Field(description="Прямая ссылка на просмотр маршрута в Яндекс.Картах")
    summary_text: str | None = Field(default=None, description="Краткое описание маршрута")


class YandexMapConfig(BaseModel):
    """Конфигурация и данные для автоматической подгрузки и рендеринга Яндекс.Карт."""

    model_config = ConfigDict(extra="ignore")

    api_key: str | None = Field(
        default=None,
        description="Ключ API Яндекс Карт (если настроен в окружении)",
    )
    script_url: str = Field(
        default="https://api-maps.yandex.ru/2.1/?lang=ru_RU",
        description="URL для загрузки JS API Яндекс Карт",
    )
    auto_load: bool = Field(
        default=True,
        description="Флаг автоматической подгрузки карты при поиске",
    )
    center: list[float] = Field(
        default_factory=lambda: [55.75222, 37.61556],
        description="Координаты центра карты [lat, lon]",
    )
    zoom: int = Field(default=12, description="Масштаб карты")
    point_a: GeoPoint | None = Field(
        default=None,
        description="Точка А (выбранный жилой комплекс ПИК)",
    )
    point_b: GeoPoint | None = Field(
        default=None,
        description="Точка Б (целевое место из запроса: метро, вуз, ориентир, парк)",
    )
    route: RouteLeg | None = Field(
        default=None,
        description="Основной маршрут между Точкой А и Точкой Б",
    )
    all_complex_routes: list[RouteLeg] = Field(
        default_factory=list,
        description="Маршруты от всех подходящих ЖК до Точки Б с расчётом времени в пути",
    )
    selected_travel_time_min: int | None = Field(
        default=None,
        description="Запрошенное пользователем ограничение по времени в пути (мин)",
    )
    travel_mode: TravelMode = Field(
        default=TravelMode.PEDESTRIAN,
        description="Определённый способ передвижения",
    )


def format_distance(distance_m: float) -> str:
    """Форматировать расстояние в метрах в читаемый вид ('850 м' или '2.4 км')."""
    if distance_m < 1000:
        return f"{round(distance_m)} м"
    km = distance_m / 1000.0
    return f"{km:.1f} км"


def format_duration(minutes: int, mode: TravelMode) -> str:
    """Форматировать время в пути в читаемый вид ('15 мин пешком')."""
    if minutes < 1:
        minutes = 1
    return f"{minutes} мин {mode.label_ru}"


def _generate_waypoints(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    steps: int = 5,
) -> list[list[float]]:
    """Сгенерировать промежуточные координаты полилинии между двумя точками.

    Создаёт плавную траекторию с небольшим изгибом, имитирующим улично-дорожный маршрут.
    """
    points: list[list[float]] = []
    mid_lat = (lat1 + lat2) / 2.0
    mid_lon = (lon1 + lon2) / 2.0
    d_lat = lat2 - lat1
    d_lon = lon2 - lon1

    offset = 0.08
    perp_lat = -d_lon * offset
    perp_lon = d_lat * offset

    for i in range(steps + 1):
        t = i / float(steps)
        b_lat = (1 - t) ** 2 * lat1 + 2 * (1 - t) * t * (mid_lat + perp_lat) + t**2 * lat2
        b_lon = (1 - t) ** 2 * lon1 + 2 * (1 - t) * t * (mid_lon + perp_lon) + t**2 * lon2
        points.append([round(b_lat, 6), round(b_lon, 6)])

    return points


async def calculate_route(
    origin: GeoPoint,
    destination: GeoPoint,
    mode: TravelMode = TravelMode.PEDESTRIAN,
    client: httpx.AsyncClient | None = None,
    api_key: str | None = None,
) -> RouteLeg:
    """Построить маршрут между Точкой А и Точкой Б.

    При наличии API-ключа Яндекс.Маршрутизации и HTTP-клиента обращается к API,
    иначе использует геометрический расчёт с учётом московской топологии и дорожных скоростей.
    """
    settings = get_settings()
    routing_key = api_key or settings.YANDEX_MAPS_ROUTING_API_KEY or settings.YANDEX_MAPS_API_KEY

    if routing_key and client:
        try:
            url = "https://api.routing.yandex.net/v2/route"
            mode_param = "walking" if mode == TravelMode.PEDESTRIAN else "driving"
            params = {
                "rtext": f"{origin.lat},{origin.lon}~{destination.lat},{destination.lon}",
                "mode": mode_param,
                "apikey": routing_key,
            }
            resp = await client.get(url, params=params, timeout=3.0)
            if resp.status_code == 200:
                data = resp.json()
                route_data = data.get("routes", [{}])[0]
                dist_m = float(route_data.get("distance", {}).get("value", 0))
                dur_s = int(route_data.get("duration", {}).get("value", 0))
                dur_m = max(1, math.ceil(dur_s / 60.0))
                raw_points = route_data.get("geometry", {}).get("coordinates", [])
                waypoints = [[p[1], p[0]] for p in raw_points] if raw_points else []
                if not waypoints:
                    waypoints = _generate_waypoints(
                        origin.lat, origin.lon, destination.lat, destination.lon
                    )

                yandex_url = (
                    f"https://yandex.ru/maps/?rtext={origin.lat},{origin.lon}~"
                    f"{destination.lat},{destination.lon}&rtt={mode.yandex_rtt}"
                )

                return RouteLeg(
                    origin=origin,
                    destination=destination,
                    travel_mode=mode,
                    distance_m=round(dist_m, 1),
                    duration_min=dur_m,
                    duration_sec=dur_s,
                    formatted_duration=format_duration(dur_m, mode),
                    formatted_distance=format_distance(dist_m),
                    waypoints=waypoints,
                    yandex_maps_url=yandex_url,
                    summary_text=(
                        f"От {origin.name or 'ЖК'} до {destination.name or 'назначения'}: "
                        f"{format_duration(dur_m, mode)} ({format_distance(dist_m)})"
                    ),
                )
        except Exception as e:
            logger.debug("Yandex Routing API fallback to internal calculation: %s", e)

    direct_dist = haversine(origin.lat, origin.lon, destination.lat, destination.lon)
    winding = WINDING_FACTORS.get(mode.value, 1.25)
    road_dist = direct_dist * winding

    speed = SPEEDS_METERS_PER_MIN.get(mode.value, 83.33)
    travel_min = road_dist / speed
    if mode == TravelMode.TRANSIT:
        travel_min += TRANSIT_FIXED_OVERHEAD_MIN

    duration_min = max(1, round(travel_min))
    duration_sec = duration_min * 60

    waypoints = _generate_waypoints(origin.lat, origin.lon, destination.lat, destination.lon)
    yandex_url = (
        f"https://yandex.ru/maps/?rtext={origin.lat},{origin.lon}~"
        f"{destination.lat},{destination.lon}&rtt={mode.yandex_rtt}"
    )

    orig_name = origin.name or "ЖК"
    dest_name = destination.name or "целевого места"

    return RouteLeg(
        origin=origin,
        destination=destination,
        travel_mode=mode,
        distance_m=round(road_dist, 1),
        duration_min=duration_min,
        duration_sec=duration_sec,
        formatted_duration=format_duration(duration_min, mode),
        formatted_distance=format_distance(road_dist),
        waypoints=waypoints,
        yandex_maps_url=yandex_url,
        summary_text=(
            f"От {orig_name} до {dest_name}: "
            f"{format_duration(duration_min, mode)} ({format_distance(road_dist)})"
        ),
    )


def detect_travel_mode(criteria: Criteria, query_text: str) -> tuple[TravelMode, int | None]:
    """Определить способ перемещения и запрошенное время по критериям и тексту."""
    text_lower = query_text.lower()

    if criteria.time_on_transport is not None:
        if any(w in text_lower for w in ["машин", "авто", "автомобил"]):
            return TravelMode.DRIVING, criteria.time_on_transport
        return TravelMode.TRANSIT, criteria.time_on_transport

    if criteria.time_on_foot is not None:
        return TravelMode.PEDESTRIAN, criteria.time_on_foot

    if any(w in text_lower for w in ["на машине", "на авто", "на автомобиле", "на такси"]):
        return TravelMode.DRIVING, None

    if any(w in text_lower for w in ["на транспорте", "на автобусе", "на метро"]):
        return TravelMode.TRANSIT, None

    if any(w in text_lower for w in ["велосипед", "самокат", "на веле"]):
        return TravelMode.BICYCLE, None

    return TravelMode.PEDESTRIAN, None


def _resolve_destination_point(criteria: Criteria, query_text: str) -> GeoPoint | None:
    """Найти Точку Б (целевое место назначения) из критериев или текста."""
    if criteria.landmark_requirements:
        lm = criteria.landmark_requirements[0]
        return GeoPoint(
            lat=lm.lat,
            lon=lm.lon,
            name=lm.name,
            address=f"Ориентир: {lm.name}",
            point_type="destination",
        )

    if criteria.metro:
        ref_metro = load_metro()
        for m in criteria.metro:
            entry = find_by_name(ref_metro, m.name)
            if entry and entry.lat is not None and entry.lon is not None:
                return GeoPoint(
                    lat=entry.lat,
                    lon=entry.lon,
                    name=f"м. {entry.name}",
                    address=f"Станция метро {entry.name}",
                    point_type="destination",
                )

    all_refs = load_all()
    query_norm = query_text.lower()
    for lm_entry in all_refs.landmarks:
        if lm_entry.lat is not None and lm_entry.lon is not None:
            if lm_entry.name.lower() in query_norm:
                return GeoPoint(
                    lat=lm_entry.lat,
                    lon=lm_entry.lon,
                    name=lm_entry.name,
                    address=f"Ориентир: {lm_entry.name}",
                    point_type="destination",
                )
            for alias in lm_entry.aliases:
                if alias.lower() in query_norm:
                    return GeoPoint(
                        lat=lm_entry.lat,
                        lon=lm_entry.lon,
                        name=lm_entry.name,
                        address=f"Ориентир: {lm_entry.name}",
                        point_type="destination",
                    )

    if criteria.center_requested:
        return GeoPoint(
            lat=55.75222,
            lon=37.61556,
            name="Центр Москвы (Кремль)",
            address="Красная площадь / Кремль",
            point_type="destination",
        )

    return None


def _get_complex_entries(
    criteria: Criteria,
    all_complexes: tuple[RefEntry, ...],
) -> list[RefEntry]:
    """Получить список ЖК с координатами из критериев или всего каталога."""
    result: list[RefEntry] = []

    if criteria.complexes:
        for c in criteria.complexes:
            entry = find_by_name(all_complexes, c.name)
            if entry and entry.lat is not None and entry.lon is not None:
                result.append(entry)

    if not result:
        result = [c for c in all_complexes if c.lat is not None and c.lon is not None]

    return result


async def build_map_context(
    criteria: Criteria,
    query_text: str,
    candidate_complexes: list[RefEntry] | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> YandexMapConfig | None:
    """Собрать полную конфигурацию Яндекс.Карт для запроса."""
    settings = get_settings()
    all_refs = load_all()
    complexes_pool = candidate_complexes or _get_complex_entries(criteria, all_refs.complexes)

    if not complexes_pool:
        return None

    point_b = _resolve_destination_point(criteria, query_text)
    travel_mode, requested_time_min = detect_travel_mode(criteria, query_text)

    if not point_b:
        first_c = complexes_pool[0]
        if first_c.lat is not None and first_c.lon is not None:
            point_a = GeoPoint(
                lat=first_c.lat,
                lon=first_c.lon,
                name=f"ЖК {first_c.name}",
                address=first_c.district or first_c.county or "Москва",
                point_type="origin",
            )
            return YandexMapConfig(
                api_key=settings.YANDEX_MAPS_API_KEY,
                auto_load=True,
                center=[point_a.lat, point_a.lon],
                zoom=14,
                point_a=point_a,
                point_b=None,
                route=None,
                all_complex_routes=[],
                selected_travel_time_min=requested_time_min,
                travel_mode=travel_mode,
            )
        return None

    all_routes: list[RouteLeg] = []
    for c_entry in complexes_pool:
        if c_entry.lat is None or c_entry.lon is None:
            continue
        c_point = GeoPoint(
            lat=c_entry.lat,
            lon=c_entry.lon,
            name=f"ЖК {c_entry.name}",
            address=c_entry.district or c_entry.county or "Москва",
            point_type="origin",
        )
        route_leg = await calculate_route(
            origin=c_point,
            destination=point_b,
            mode=travel_mode,
            client=http_client,
            api_key=settings.YANDEX_MAPS_ROUTING_API_KEY or settings.YANDEX_MAPS_API_KEY,
        )
        all_routes.append(route_leg)

    if not all_routes:
        return None

    all_routes.sort(key=lambda r: r.duration_sec)

    chosen_route = all_routes[0]
    if requested_time_min is not None:
        matching_routes = [r for r in all_routes if r.duration_min <= requested_time_min]
        chosen_route = matching_routes[0] if matching_routes else all_routes[0]

    point_a = chosen_route.origin

    center_lat = (point_a.lat + point_b.lat) / 2.0
    center_lon = (point_a.lon + point_b.lon) / 2.0
    dist_m = chosen_route.distance_m

    if dist_m < 1500:
        zoom = 15
    elif dist_m < 4000:
        zoom = 14
    elif dist_m < 10000:
        zoom = 12
    elif dist_m < 25000:
        zoom = 11
    else:
        zoom = 10

    script_url = "https://api-maps.yandex.ru/2.1/?lang=ru_RU"
    if settings.YANDEX_MAPS_API_KEY:
        script_url = (
            f"https://api-maps.yandex.ru/2.1/?lang=ru_RU&apikey={settings.YANDEX_MAPS_API_KEY}"
        )

    return YandexMapConfig(
        api_key=settings.YANDEX_MAPS_API_KEY,
        script_url=script_url,
        auto_load=True,
        center=[round(center_lat, 6), round(center_lon, 6)],
        zoom=zoom,
        point_a=point_a,
        point_b=point_b,
        route=chosen_route,
        all_complex_routes=all_routes[:10],
        selected_travel_time_min=requested_time_min or chosen_route.duration_min,
        travel_mode=travel_mode,
    )
