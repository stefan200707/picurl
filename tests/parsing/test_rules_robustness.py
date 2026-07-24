"""Системная устойчивость парсера (6 классов ошибок из живого запроса).

Ложные срабатывания дорог vs метро-кольцо, идиомы времени до метро, дистанция в
минутах (+ ведущая дистанция для POI), раздельные санузлы (ед. vs мн.), и
распознавание «внутри/за МКАД». Антирегрессии зафиксированы явно.
"""

import pytest

from app.geo.poi import POICategory
from app.parsing.parser import parse
from app.parsing.rules.misc import extract_within_mkad
from app.parsing.rules.poi import extract_poi_requirements
from app.parsing.rules.station_class import extract_station_class_requirements
from app.parsing.rules.time import extract_time_to_metro


# --- Фаза A: дороги vs метро-кольцо (ложные срабатывания) -------------------
@pytest.mark.parametrize(
    "text",
    [
        "внутри МКАД кольца",
        "за ЦКАД",
        "Садовое кольцо",
        "дом у ТТК",
        "третье транспортное кольцо",
    ],
)
def test_road_ring_not_station(text: str) -> None:
    assert extract_station_class_requirements(text)[0] == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("кольцевая линия", "Кольцевая"),
        ("по кольцевой ветке", "Кольцевая"),
        ("рядом с БКЛ", "Большая кольцевая"),
    ],
)
def test_real_ring_line_still_matches(text: str, expected: str) -> None:
    reqs = extract_station_class_requirements(text)[0]
    assert expected in [r.line_prefix for r in reqs]


# --- Фаза B: время до метро (идиома «доступности») --------------------------
@pytest.mark.parametrize(
    ("text", "foot", "trans"),
    [
        ("метро было в доступности 15 минут", 15, None),
        ("в шаговой доступности 7 минут", 7, None),
        ("10 минут ходьбы до метро", 10, None),
        ("20 минут на машине до метро", None, 20),
    ],
)
def test_time_to_metro_idioms(text: str, foot: int | None, trans: int | None) -> None:
    t = extract_time_to_metro(text)[0]
    assert t.time_on_foot == foot
    assert t.time_on_transport == trans


# --- Фаза C: дистанция в минутах + ведущая дистанция для POI -----------------
def test_poi_distance_in_minutes() -> None:
    reqs = extract_poi_requirements("школа в 15 минутах")[0]
    assert reqs[0].category is POICategory.SCHOOL
    assert reqs[0].max_distance_m == 1200  # 15 * 80 м/мин


def test_poi_leading_distance_applies_to_all() -> None:
    reqs = extract_poi_requirements("рядом вблизи до 18 минут школы и детские сады")[0]
    cats = {r.category: r.max_distance_m for r in reqs}
    assert cats[POICategory.SCHOOL] == 1440  # 18 * 80
    assert cats[POICategory.KINDERGARTEN] == 1440


def test_metro_time_not_stolen_by_poi_distance() -> None:
    # «рядом с метро 10 минут» — время до метро, а не дистанция до POI.
    reqs = extract_poi_requirements("рядом с метро 10 минут и школа")[0]
    school = [r for r in reqs if r.category is POICategory.SCHOOL]
    assert school and school[0].max_distance_m is None


# --- Фаза E: распознавание «внутри/за МКАД» ---------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("внутри МКАД", True),
        ("в пределах МКАД", True),
        ("внутри МКАД кольца", True),
        ("за МКАДом", False),
        ("за пределами МКАД", False),
        ("вне МКАД", False),
        ("просто квартира", None),
    ],
)
def test_within_mkad_rule(text: str, expected: bool | None) -> None:
    assert extract_within_mkad(text)[0] is expected


# --- Фаза D: раздельные санузлы (мн. → manybathrooms, ед. → не поддерживается) --
def test_bathrooms_plural_maps_to_manybathrooms() -> None:
    r = parse("раздельные санузлы")
    assert "manybathrooms" in r.criteria.option_groups
    assert r.warnings == []


def test_bathrooms_singular_stays_unsupported() -> None:
    r = parse("раздельный санузел")
    assert r.criteria.option_groups == []
    assert any("не поддерживается" in w for w in r.warnings)
