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
from app.parsing.schema import (
    Criteria,
    LandmarkRequirement,
    MatchedEntity,
    StationClassRequirement,
)


@pytest.fixture
def ref_dir(tmp_path, monkeypatch):
    """Временный справочник с тремя ЖК в разных районах и одним центральным."""
    (tmp_path / "complexes.json").write_text(
        json.dumps(
            [
                {"name": "Центральный", "slug": "c1", "id": "1", "district": "Арбат"},
                {
                    "name": "Восточный",
                    "slug": "c2",
                    "id": "2",
                    "district": "Гольяново",
                    "metro": "Щёлковская",
                },
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
    for name in ("metro", "counties", "benefits", "option_groups", "options", "landmarks"):
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


@pytest.fixture
def geo_ref_dir(tmp_path, monkeypatch):
    """Справочник ЖК с координатами (для ранжирования по дистанции)."""
    (tmp_path / "complexes.json").write_text(
        json.dumps(
            [
                # Рядом с МГУ (координаты 55.7033, 37.5308).
                {"name": "У МГУ", "slug": "near", "id": "1", "lat": 55.7050, "lon": 37.5320},
                # В центре Москвы — заметно дальше от МГУ.
                {"name": "В центре", "slug": "mid", "id": "2", "lat": 55.7520, "lon": 37.6175},
                # На севере — дальше всех.
                {"name": "Далеко", "slug": "far", "id": "3", "lat": 55.9000, "lon": 37.4000},
                # Без координат — близость неизвестна.
                {"name": "Без координат", "slug": "nogeo", "id": "4"},
            ],
            ensure_ascii=False,
        ),
        "utf-8",
    )
    for name in ("metro", "counties", "districts", "benefits", "option_groups", "options"):
        (tmp_path / f"{name}.json").write_text("[]", "utf-8")
    (tmp_path / "landmarks.json").write_text("[]", "utf-8")
    (tmp_path / "poi_cache.json").write_text("{}", "utf-8")

    monkeypatch.setattr("app.reference.loader.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.geo.candidates.DATA_DIR", tmp_path)
    from app.reference.loader import clear_cache

    clear_cache()
    yield tmp_path
    clear_cache()


#: Координаты МГУ (как в landmarks.json).
_MGU = LandmarkRequirement(name="МГУ им. Ломоносова", lat=55.703326, lon=37.530762)


def test_shortlist_ranked_by_landmark_distance(geo_ref_dir):
    """«рядом с МГУ» → шорт-лист отсортирован по дистанции, детерминированно."""
    criteria = Criteria(landmark_requirements=[_MGU])
    candidates = build_candidate_shortlist(criteria)

    ranked = [c.name for c in candidates]
    # Ближайший к МГУ — первым, далёкий — позже; ЖК без координат — в конце.
    assert ranked.index("У МГУ") < ranked.index("В центре")
    assert ranked.index("В центре") < ranked.index("Далеко")
    assert ranked[-1] == "Без координат"
    # Координаты прокинуты в кандидатов (объективный факт для расчёта).
    assert candidates[0].lat is not None


def test_shortlist_landmark_max_distance_filters(geo_ref_dir):
    """Жёсткая отсечка по max_distance_m убирает далёкие ЖК и ЖК без координат."""
    near_mgu = LandmarkRequirement(
        name="МГУ им. Ломоносова", lat=55.703326, lon=37.530762, max_distance_m=3000
    )
    criteria = Criteria(landmark_requirements=[near_mgu])
    names = {c.name for c in build_candidate_shortlist(criteria)}
    assert names == {"У МГУ"}


def test_known_facts_landmark_filters_by_default_radius(geo_ref_dir):
    """Без явной дистанции сужение по ориентиру берёт радиус по умолчанию.

    Регрессия бага AI-12 («однушка рядом с МГУ подешевле»): ориентир без явной
    дистанции («в 500 метрах») не должен деградировать до «не сузили вообще».
    """
    criteria = Criteria(landmark_requirements=[_MGU])
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    # Только ближайший к МГУ ЖК проходит радиус по умолчанию (3 км).
    assert known["matched_complex_ids"] == ["1"]


def test_known_facts_landmark_respects_explicit_max_distance(geo_ref_dir):
    """Явная дистанция от пользователя переопределяет радиус по умолчанию."""
    near_mgu = LandmarkRequirement(
        name="МГУ им. Ломоносова", lat=55.703326, lon=37.530762, max_distance_m=100
    )
    criteria = Criteria(landmark_requirements=[near_mgu])
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    # "У МГУ" дальше 100 м от точки МГУ — под жёсткую отсечку не попадает.
    assert known["matched_complex_ids"] == []


def test_fully_resolved_false_when_landmark_coords_unknown(geo_ref_dir):
    """ЖК без координат в кандидатах не даёт считать решение полным."""
    criteria = Criteria(landmark_requirements=[_MGU])
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    assert any(c.lat is None for c in candidates)  # "Без координат" среди кандидатов
    assert fully_resolved(known, criteria, candidates) is False


def test_fully_resolved_true_when_all_landmark_coords_known(geo_ref_dir):
    """Если координаты известны у всех кандидатов — решение по ориентиру полное."""
    criteria = Criteria(landmark_requirements=[_MGU])
    candidates = [c for c in build_candidate_shortlist(criteria) if c.lat is not None]
    known = resolve_known_facts(candidates, criteria)

    assert fully_resolved(known, criteria, candidates) is True


@pytest.fixture
def station_class_ref_dir(tmp_path, monkeypatch):
    """Справочник со станциями метро (line/lat/lon) и ЖК на разном расстоянии
    от станции класса МЦД-2 (Milestone AI-15: «рядом с МЦД не важно какой
    станции»)."""
    (tmp_path / "metro.json").write_text(
        json.dumps(
            [
                # Станция линии МЦД-2 с координатами.
                {"name": "Санино", "id": "m1", "lat": 55.8000, "lon": 37.6000, "line": "МЦД-2"},
                # Обычная линия метро (не МЦД/МЦК) — не должна матчиться на "МЦД".
                {
                    "name": "Курская",
                    "id": "m2",
                    "lat": 55.7580,
                    "lon": 37.6600,
                    "line": "Арбатско-Покровская",
                },
                # Станция МЦД-2 без координат — не может участвовать в сужении.
                {"name": "Без координат", "id": "m3", "line": "МЦД-2"},
            ],
            ensure_ascii=False,
        ),
        "utf-8",
    )
    (tmp_path / "complexes.json").write_text(
        json.dumps(
            [
                # В 100 м от станции "Санино" (МЦД-2).
                {
                    "name": "У Санино",
                    "slug": "near",
                    "id": "1",
                    "lat": 55.8009,
                    "lon": 37.6000,
                },
                # Далеко от любой станции класса МЦД.
                {"name": "Далеко", "slug": "far", "id": "2", "lat": 56.2000, "lon": 37.0000},
                # Без координат — близость неизвестна.
                {"name": "Без координат", "slug": "nogeo", "id": "3"},
            ],
            ensure_ascii=False,
        ),
        "utf-8",
    )
    for name in ("counties", "districts", "benefits", "option_groups", "options"):
        (tmp_path / f"{name}.json").write_text("[]", "utf-8")
    (tmp_path / "landmarks.json").write_text("[]", "utf-8")
    (tmp_path / "poi_cache.json").write_text("{}", "utf-8")

    monkeypatch.setattr("app.reference.loader.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.geo.candidates.DATA_DIR", tmp_path)
    from app.reference.loader import clear_cache

    clear_cache()
    yield tmp_path
    clear_cache()


#: Требование «рядом с МЦД не важно какой станции» (как в живом запросе-баге).
_MCD = StationClassRequirement(line_prefix="МЦД")


def test_station_class_points_matches_prefix_not_other_lines(station_class_ref_dir):
    """ "МЦД" матчит станции линии "МЦД-2" (по префиксу), но не обычное метро."""
    from app.geo.candidates import station_class_points

    criteria = Criteria(station_class_requirements=[_MCD])
    points = station_class_points(criteria)
    # Только "Санино" (МЦД-2, есть координаты) — "Курская" другая линия,
    # "Без координат" отсеяна отсутствием lat/lon.
    assert points == [(55.8000, 37.6000)]


def test_station_class_points_any_metro_includes_everything_with_coords(station_class_ref_dir):
    """line_prefix="метро" — любая станция вообще, включая МЦД (без фильтра линии)."""
    from app.geo.candidates import station_class_points

    criteria = Criteria(station_class_requirements=[StationClassRequirement(line_prefix="метро")])
    points = station_class_points(criteria)
    assert set(points) == {(55.8000, 37.6000), (55.7580, 37.6600)}


def test_shortlist_ranked_by_station_class_distance(station_class_ref_dir):
    """«рядом с МЦД не важно какой станции» → шорт-лист отсортирован по
    дистанции до БЛИЖАЙШЕЙ станции класса, детерминированно."""
    criteria = Criteria(station_class_requirements=[_MCD])
    candidates = build_candidate_shortlist(criteria)

    ranked = [c.name for c in candidates]
    assert ranked.index("У Санино") < ranked.index("Далеко")
    assert ranked[-1] == "Без координат"


def test_shortlist_station_class_max_distance_filters(station_class_ref_dir):
    """Жёсткая отсечка по max_distance_m убирает далёкие ЖК и ЖК без координат."""
    near = StationClassRequirement(line_prefix="МЦД", max_distance_m=500)
    criteria = Criteria(station_class_requirements=[near])
    names = {c.name for c in build_candidate_shortlist(criteria)}
    assert names == {"У Санино"}


def test_known_facts_station_class_filters_by_default_radius(station_class_ref_dir):
    """Без явной дистанции сужение по классу станций берёт радиус по умолчанию
    (STATION_CLASS_DEFAULT_RADIUS_M = 1500 м, пешая доступность)."""
    criteria = Criteria(station_class_requirements=[_MCD])
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    assert known["matched_complex_ids"] == ["1"]


def test_known_facts_station_class_respects_explicit_max_distance(station_class_ref_dir):
    """Явная дистанция от пользователя переопределяет радиус по умолчанию."""
    near = StationClassRequirement(line_prefix="МЦД", max_distance_m=50)
    criteria = Criteria(station_class_requirements=[near])
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    # "У Санино" дальше 50 м от станции — под жёсткую отсечку не попадает.
    assert known["matched_complex_ids"] == []


def test_fully_resolved_false_when_station_class_coords_unknown(station_class_ref_dir):
    """ЖК без координат в кандидатах не даёт считать решение полным."""
    criteria = Criteria(station_class_requirements=[_MCD])
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    assert any(c.lat is None for c in candidates)
    assert fully_resolved(known, criteria, candidates) is False


def test_fully_resolved_true_when_all_station_class_coords_known(station_class_ref_dir):
    """Если координаты известны у всех кандидатов — решение по классу полное."""
    criteria = Criteria(station_class_requirements=[_MCD])
    candidates = [c for c in build_candidate_shortlist(criteria) if c.lat is not None]
    known = resolve_known_facts(candidates, criteria)

    assert fully_resolved(known, criteria, candidates) is True


def test_fully_resolved_false_when_no_station_has_coordinates(ref_dir):
    """Ни у одной станции подходящего класса нет координат (metro.json ещё не
    обогащён line/lat/lon) — сужение физически невозможно, решение неполное.
    Это же условие в app.ai.enrichment превращается в явный warning, а не
    молчаливый пропуск (инвариант «ничего не отбрасывается молча»)."""
    criteria = Criteria(station_class_requirements=[_MCD])
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    assert known["matched_complex_ids"] == []
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
