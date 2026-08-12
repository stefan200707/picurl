"""Тесты для интеграции с Яндекс.Картами и построения маршрутов."""

import pytest

from app.geo.yandex_maps import (
    GeoPoint,
    RouteLeg,
    TravelMode,
    _generate_waypoints,
    _resolve_destination_point,
    build_map_context,
    calculate_route,
    detect_travel_mode,
    format_distance,
    format_duration,
)
from app.parsing.schema import Criteria, LandmarkRequirement, MatchedEntity


def test_format_distance():
    assert format_distance(450.0) == "450 м"
    assert format_distance(1200.0) == "1.2 км"
    assert format_distance(25400.0) == "25.4 км"


def test_format_duration():
    assert format_duration(15, TravelMode.PEDESTRIAN) == "15 мин пешком"
    assert format_duration(8, TravelMode.DRIVING) == "8 мин на машине"
    assert format_duration(25, TravelMode.TRANSIT) == "25 мин на транспорте"
    assert format_duration(12, TravelMode.BICYCLE) == "12 мин на велосипеде"
    assert format_duration(0, TravelMode.PEDESTRIAN) == "1 мин пешком"


def test_generate_waypoints():
    pts = _generate_waypoints(55.75, 37.61, 55.70, 37.53, steps=4)
    assert len(pts) == 5
    assert pts[0] == [55.75, 37.61]
    assert pts[-1] == [55.70, 37.53]


@pytest.mark.asyncio
async def test_calculate_route_pedestrian():
    origin = GeoPoint(lat=55.7029, lon=37.5308, name="ЖК Матвеевский парк")
    destination = GeoPoint(lat=55.7028, lon=37.5305, name="МГУ")

    route = await calculate_route(origin, destination, mode=TravelMode.PEDESTRIAN)
    assert isinstance(route, RouteLeg)
    assert route.origin == origin
    assert route.destination == destination
    assert route.travel_mode == TravelMode.PEDESTRIAN
    assert route.duration_min >= 1
    assert route.distance_m > 0
    assert len(route.waypoints) >= 2
    assert "yandex.ru/maps" in route.yandex_maps_url
    assert "rtt=pd" in route.yandex_maps_url


@pytest.mark.asyncio
async def test_calculate_route_driving():
    origin = GeoPoint(lat=55.80, lon=37.75, name="ЖК Амурский парк")
    destination = GeoPoint(lat=55.7522, lon=37.6155, name="Кремль")

    route = await calculate_route(origin, destination, mode=TravelMode.DRIVING)
    assert route.travel_mode == TravelMode.DRIVING
    assert "rtt=auto" in route.yandex_maps_url
    assert route.duration_min >= 1


def test_detect_travel_mode():
    c_foot = Criteria(time_on_foot=15)
    mode, t = detect_travel_mode(c_foot, "до метро 15 минут пешком")
    assert mode == TravelMode.PEDESTRIAN
    assert t == 15

    c_trans = Criteria(time_on_transport=20)
    mode, t = detect_travel_mode(c_trans, "20 минут на машине до центра")
    assert mode == TravelMode.DRIVING
    assert t == 20

    c_auto_text = Criteria()
    mode, t = detect_travel_mode(c_auto_text, "квартира до 25 мин на авто")
    assert mode == TravelMode.DRIVING

    c_transit_text = Criteria()
    mode, t = detect_travel_mode(c_transit_text, "до 30 мин на транспорте")
    assert mode == TravelMode.TRANSIT


def test_resolve_destination_point_landmark():
    c = Criteria(
        landmark_requirements=[
            LandmarkRequirement(name="МГУ им. Ломоносова", lat=55.7028, lon=37.5305)
        ]
    )
    dest = _resolve_destination_point(c, "квартира у МГУ")
    assert dest is not None
    assert dest.name == "МГУ им. Ломоносова"
    assert dest.lat == 55.7028
    assert dest.point_type == "destination"


def test_resolve_destination_point_metro():
    c = Criteria(metro=[MatchedEntity(name="Черкизовская")])
    dest = _resolve_destination_point(c, "у метро Черкизовская")
    assert dest is not None
    assert "Черкизовская" in dest.name
    assert dest.lat is not None


@pytest.mark.asyncio
async def test_build_map_context_with_landmark_and_time():
    c = Criteria(
        time_on_foot=20,
        landmark_requirements=[
            LandmarkRequirement(name="МГУ им. Ломоносова", lat=55.7028, lon=37.5305)
        ],
    )
    map_config = await build_map_context(c, "двушка до 20 минут пешком от МГУ")

    assert map_config is not None
    assert map_config.auto_load is True
    assert map_config.point_a is not None
    assert map_config.point_b is not None
    assert "МГУ" in (map_config.point_b.name or "")
    assert map_config.route is not None
    assert map_config.route.duration_min >= 1
    assert map_config.selected_travel_time_min == 20
    assert len(map_config.all_complex_routes) > 0
    # Проверяем, что список отсортирован по возрастанию времени
    durations = [r.duration_sec for r in map_config.all_complex_routes]
    assert durations == sorted(durations)
