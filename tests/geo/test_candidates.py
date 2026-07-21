"""Тесты шорт-листа ИИ-кандидатов и детерминизма центра (правки п.1-3).

Проверяют, что кандидаты уходят в модель с реальным гео-контекстом (район/
округ/метро/центр), а шорт-лист сужается по локации из запроса, а не берёт
произвольные «первые N». Справочники подменяются на временный каталог.
"""

import json

import pytest

from app.geo.candidates import (
    build_candidate_shortlist,
    fully_resolved,
    resolve_known_facts,
)
from app.parsing.schema import Criteria, MatchedEntity


@pytest.fixture
def ref_dir(tmp_path, monkeypatch):
    """Временный справочник с тремя ЖК в разных районах и одним центральным."""
    (tmp_path / "complexes.json").write_text(
        json.dumps(
            [
                {"name": "Центральный", "slug": "c1", "id": "1", "district": "Арбат"},
                {"name": "Восточный", "slug": "c2", "id": "2", "district": "Гольяново",
                 "metro": "Щёлковская"},
                {"name": "Западный", "slug": "c3", "id": "3", "county": "ЗАО"},
            ],
            ensure_ascii=False,
        ),
        "utf-8",
    )
    (tmp_path / "districts.json").write_text(
        json.dumps(
            [
                {"name": "Арбат", "id": "d1", "is_center": True},
                {"name": "Гольяново", "id": "d2", "is_center": False},
            ],
            ensure_ascii=False,
        ),
        "utf-8",
    )
    for name in ("metro", "counties", "benefits", "option_groups", "options"):
        (tmp_path / f"{name}.json").write_text("[]", "utf-8")
    (tmp_path / "poi_cache.json").write_text("{}", "utf-8")

    monkeypatch.setattr("app.reference.loader.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.geo.candidates.DATA_DIR", tmp_path)
    from app.reference.loader import clear_cache

    clear_cache()
    yield tmp_path
    clear_cache()


def test_shortlist_fills_location_context(ref_dir):
    """Кандидаты получают район/метро/округ и вычисленный is_center."""
    candidates = build_candidate_shortlist(Criteria())
    by_name = {c.name: c for c in candidates}

    assert by_name["Центральный"].district == "Арбат"
    assert by_name["Центральный"].is_center is True
    assert by_name["Восточный"].metro == ["Щёлковская"]
    assert by_name["Восточный"].is_center is False
    assert by_name["Западный"].county == "ЗАО"
    assert by_name["Западный"].is_center is None  # район не задан — центр неизвестен


def test_shortlist_filters_by_requested_district(ref_dir):
    """Запрос сужен районом → в шорт-лист попадают только совпадающие ЖК."""
    criteria = Criteria(districts=[MatchedEntity(name="Гольяново", id="d2")])
    names = {c.name for c in build_candidate_shortlist(criteria)}
    assert names == {"Восточный"}


def test_shortlist_explicit_complexes_take_priority(ref_dir):
    """Явно названные ЖК имеют приоритет над гео-фильтром."""
    criteria = Criteria(complexes=[MatchedEntity(name="Западный", id="3")])
    names = {c.name for c in build_candidate_shortlist(criteria)}
    assert names == {"Западный"}


def test_center_resolved_deterministically(ref_dir):
    """«В центре» без POI разрешается из справочника, без похода в ИИ."""
    criteria = Criteria(center_requested=True)
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    # Центральные районы взяты из справочника.
    assert known["center_district_ids"] == ["d1"]
    # Совпадением по центру считается только заведомо центральный ЖК.
    assert known["matched_complex_ids"] == ["1"]
    # is_center известен не у всех (Западный=None) → детерминизм невозможен.
    assert fully_resolved(known, criteria, candidates) is False


def test_center_fully_resolved_when_all_known(ref_dir):
    """Если центр известен у всех кандидатов — ИИ не нужен."""
    criteria = Criteria(
        center_requested=True,
        # Сужаем до районов с известным is_center (Арбат/Гольяново).
        districts=[MatchedEntity(name="Арбат", id="d1"), MatchedEntity(name="Гольяново", id="d2")],
    )
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    assert all(c.is_center is not None for c in candidates)
    assert fully_resolved(known, criteria, candidates) is True
