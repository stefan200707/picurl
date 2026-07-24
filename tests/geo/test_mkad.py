"""Гео-сужение «внутри/за МКАД» (Фаза E).

point_in_mkad на реальных координатах Москвы/области, разбиение справочника ЖК
и сужение по blocks (с пересечением при наличии других локаций).
"""

from app.geo.candidates import complexes_in_mkad
from app.geo.mkad import point_in_mkad
from app.parsing.schema import Criteria, MatchedEntity
from app.pik.location_fallback import resolve_fallback_block_ids


def test_point_in_mkad_center_inside() -> None:
    assert point_in_mkad(55.751, 37.617) is True  # Кремль


def test_point_in_mkad_suburb_outside() -> None:
    assert point_in_mkad(55.991, 37.214) is False  # Зеленоград
    assert point_in_mkad(55.889, 37.430) is False  # Химки


def test_complexes_split_inside_outside_disjoint() -> None:
    inside = set(complexes_in_mkad(True))
    outside = set(complexes_in_mkad(False))
    assert inside and outside  # обе стороны непусты
    assert inside.isdisjoint(outside)  # ЖК не может быть по обе стороны


def test_within_mkad_produces_blocks() -> None:
    fb = resolve_fallback_block_ids(Criteria(within_mkad=True))
    assert fb.block_ids
    assert set(fb.block_ids) == set(complexes_in_mkad(True))


def test_beyond_mkad_is_inverse() -> None:
    inside = resolve_fallback_block_ids(Criteria(within_mkad=True)).block_ids
    outside = resolve_fallback_block_ids(Criteria(within_mkad=False)).block_ids
    assert set(inside).isdisjoint(set(outside))


def test_within_mkad_intersects_other_fallback() -> None:
    # Район без id уходит в тег-фолбэк → его block_ids пересекаются с внутри-МКАД.
    all_inside = set(complexes_in_mkad(True))
    criteria = Criteria(
        within_mkad=True,
        districts=[MatchedEntity(name="Хамовники", slug=None, id=None)],
    )
    fb = resolve_fallback_block_ids(criteria)
    # Всё, что осталось, обязано быть внутри МКАД (пересечение, не объединение).
    assert set(fb.block_ids) <= all_inside
