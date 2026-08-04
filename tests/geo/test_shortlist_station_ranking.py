"""Г3: шорт-лист для ненайденной локации ранжируется по дистанции до станции.

Раньше при ненайденной по тегу локации шорт-лист откатывался на «первые 50 ЖК
из справочника по порядку файла», а честно посчитанные haversine-ближайшие жили
в параллельной ветке (``app.pik.location_fallback``) и до модели не доезжали.
Модель получала произвольный список без единого признака запрошенной станции и
закономерно отвечала «подходящих нет».
"""

from app.geo.candidates import build_candidate_shortlist
from app.geo.distance import haversine
from app.parsing.schema import Criteria, MatchedEntity
from app.reference.loader import find_by_name, load_complexes, load_metro


def _station(name: str = "ВДНХ"):
    entry = find_by_name(load_metro(), name)
    assert entry is not None and entry.lat is not None and entry.lon is not None
    return entry


def _criteria(name: str = "ВДНХ") -> Criteria:
    return Criteria(metro=[MatchedEntity(name=name, slug=None, id=None)])


def test_shortlist_is_sorted_by_distance_to_requested_station():
    station = _station()
    candidates = build_candidate_shortlist(_criteria(), [])

    assert candidates, "фолбэк обязан вернуть кандидатов"
    distances = [c.distance_to_location_m for c in candidates]
    assert distances[0] is not None
    known = [d for d in distances if d is not None]
    assert known == sorted(known), "кандидаты обязаны идти по возрастанию дистанции"

    nearest = min(
        haversine(station.lat, station.lon, c.lat, c.lon)
        for c in load_complexes()
        if c.lat is not None and c.lon is not None
    )
    assert abs(candidates[0].distance_to_location_m - nearest) < 1.0


def test_shortlist_truncation_happens_after_sorting():
    """Инвариант 13: усечение до лимита — ПОСЛЕ сортировки по дистанции.

    Иначе «первые 50 из файла» отсекли бы реально ближайшие ЖК.
    """
    from app.geo.candidates import SHORTLIST_LIMIT

    station = _station()
    candidates = build_candidate_shortlist(_criteria(), [])

    assert len(candidates) <= SHORTLIST_LIMIT
    returned = {c.id for c in candidates}
    all_ranked = sorted(
        (haversine(station.lat, station.lon, c.lat, c.lon), c.id)
        for c in load_complexes()
        if c.lat is not None and c.lon is not None
    )
    expected_top = {cid for _d, cid in all_ranked[: min(10, len(all_ranked))]}
    assert expected_top <= returned


def test_shortlist_warning_says_list_is_nearest_not_citywide():
    warnings: list[str] = []
    build_candidate_shortlist(_criteria(), warnings)

    assert any("ближайш" in w for w in warnings), warnings
    assert not any("по всему городу" in w for w in warnings), warnings
