"""Качество POI-данных: действующие объекты vs стройплощадки (шаг 4).

Живой факт, из-за которого этот файл существует: пользователь открыл ссылку с
``blocks=1165,518,1372``, где кэш обещал детский сад в 186 м от ЖК «Нарвин», и
садов на карте не нашёл. Проверка живым Overpass показала, что кэш
воспроизводит OSM до второго знака, но объект негодный: ``relation/13512774`` —
``amenity=kindergarten`` + ``construction=education`` +
``construction:type=construction`` + ``landuse=construction``, без ``name``, то
есть огороженная СТРОЙПЛОЩАДКА. Первый работающий сад — «Детский сад №2044» в
300 м.

Границы правки (осознанно узкие): исключаются ТОЛЬКО нерабочие объекты
(``construction:``/``planned:``/``proposed:``). Дошкольные корпуса школьных
комплексов («Школа № 1576. Корпус № 16») — настоящие работающие детсады
московской системы образования и остаются; безымянные полигоны и частные сети
тоже остаются, но их особенность видна в данных.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.geo.candidates import build_candidate_shortlist, resolve_known_facts
from app.geo.poi import POI_CACHE_SCHEMA_VERSION, POICategory, fetch_poi
from app.parsing.schema import Criteria, POIRequirement

#: Координаты ЖК «Нарвин» (app/reference/complexes.json, id=1165).
NARVIN_LAT = 55.842036
NARVIN_LON = 37.499994

#: Метров в градусе широты — для сборки фикстуры на заданной дистанции.
_M_PER_DEG_LAT = 111_320.0


def _north_of_narvin(meters: float) -> float:
    """Широта точки в ``meters`` строго к северу от «Нарвина»."""
    return NARVIN_LAT + meters / _M_PER_DEG_LAT


#: Реальные теги relation/13512774 (проверено живым Overpass 2026-07): сад
#: значится amenity=kindergarten, но это стройплощадка без названия.
CONSTRUCTION_KINDERGARTEN_TAGS = {
    "amenity": "kindergarten",
    "construction": "education",
    "construction:type": "construction",
    "landuse": "construction",
    "barrier": "fence",
}


def _overpass_payload() -> dict:
    """Ответ Overpass: стройка в 186 м и работающий сад в 300 м."""
    return {
        "elements": [
            {
                "type": "relation",
                "id": 13512774,
                "center": {"lat": _north_of_narvin(186), "lon": NARVIN_LON},
                "tags": CONSTRUCTION_KINDERGARTEN_TAGS,
            },
            {
                "type": "node",
                "id": 1,
                "lat": _north_of_narvin(300),
                "lon": NARVIN_LON,
                "tags": {"amenity": "kindergarten", "name": "Детский сад №2044"},
            },
        ]
    }


@pytest.fixture
def narvin_ref_dir(tmp_path, monkeypatch):
    """Справочник из одного ЖК «Нарвин» с пустым POI-кэшем."""
    (tmp_path / "complexes.json").write_text(
        json.dumps(
            [
                {
                    "name": "Нарвин",
                    "slug": "narvin",
                    "id": "1165",
                    "lat": NARVIN_LAT,
                    "lon": NARVIN_LON,
                }
            ],
            ensure_ascii=False,
        ),
        "utf-8",
    )
    for name in (
        "metro",
        "counties",
        "districts",
        "benefits",
        "option_groups",
        "options",
        "landmarks",
    ):
        (tmp_path / f"{name}.json").write_text("[]", "utf-8")
    (tmp_path / "poi_cache.json").write_text("{}", "utf-8")

    monkeypatch.setattr("app.reference.loader.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.geo.candidates.DATA_DIR", tmp_path)
    from app.reference.loader import clear_cache

    clear_cache()
    yield tmp_path
    clear_cache()


def _kindergarten_criteria(max_distance_m: int) -> Criteria:
    return Criteria(
        poi_requirements=[
            POIRequirement(
                category=POICategory.KINDERGARTEN,
                raw_phrase="садик",
                max_distance_m=max_distance_m,
            )
        ]
    )


@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_construction_kindergarten_does_not_satisfy_200m(mock_post, narvin_ref_dir):
    """Стройка в 186 м не пускает ЖК в «садик в 200 метрах».

    Именно этот путь и врал в живом прогоне: closest_distance_m считался по
    ВСЕМ элементам ответа Overpass, включая construction:-теги, и «Нарвин»
    проходил требование по стройплощадке.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = _overpass_payload()
    mock_post.return_value = mock_response

    result = await fetch_poi(NARVIN_LAT, NARVIN_LON, POICategory.KINDERGARTEN, 2000)
    (narvin_ref_dir / "poi_cache.json").write_text(
        json.dumps({"narvin": {"kindergarten": result.model_dump()}}, ensure_ascii=False), "utf-8"
    )

    criteria = _kindergarten_criteria(200)
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    assert known["matched_complex_ids"] == []


@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_working_kindergarten_satisfies_350m(mock_post, narvin_ref_dir):
    """Работающий сад в 300 м пускает ЖК в «садик в 350 метрах»."""
    mock_response = MagicMock()
    mock_response.json.return_value = _overpass_payload()
    mock_post.return_value = mock_response

    result = await fetch_poi(NARVIN_LAT, NARVIN_LON, POICategory.KINDERGARTEN, 2000)
    (narvin_ref_dir / "poi_cache.json").write_text(
        json.dumps({"narvin": {"kindergarten": result.model_dump()}}, ensure_ascii=False), "utf-8"
    )

    criteria = _kindergarten_criteria(350)
    candidates = build_candidate_shortlist(criteria)
    known = resolve_known_facts(candidates, criteria)

    assert known["matched_complex_ids"] == ["1165"]


@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_shortlist_carries_construction_distance_and_schema(mock_post, narvin_ref_dir):
    """Дистанция до стройки и версия схемы доезжают до кандидата.

    Без этой проводки ``closest_under_construction_m`` оставался числом, которое
    считается и кэшируется, но никем не читается, — а это ровно то число, что
    объясняет живой случай «сад в 186 м» (в 186 м был котлован).
    """
    mock_response = MagicMock()
    mock_response.json.return_value = _overpass_payload()
    mock_post.return_value = mock_response

    result = await fetch_poi(NARVIN_LAT, NARVIN_LON, POICategory.KINDERGARTEN, 2000)
    (narvin_ref_dir / "poi_cache.json").write_text(
        json.dumps({"narvin": {"kindergarten": result.model_dump()}}, ensure_ascii=False), "utf-8"
    )

    candidate = build_candidate_shortlist(_kindergarten_criteria(350))[0]

    assert candidate.poi_under_construction_m["kindergarten"] == pytest.approx(186, abs=5)
    assert candidate.poi_schema_version["kindergarten"] == POI_CACHE_SCHEMA_VERSION


@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_fetch_poi_keeps_construction_as_separate_fact(mock_post):
    """Стройка не исчезает молча: отдельный счётчик и своя дистанция."""
    mock_response = MagicMock()
    mock_response.json.return_value = _overpass_payload()
    mock_post.return_value = mock_response

    result = await fetch_poi(NARVIN_LAT, NARVIN_LON, POICategory.KINDERGARTEN, 2000)

    assert result.count == 2
    assert result.count_operational == 1
    assert result.count_under_construction == 1
    assert result.closest_distance_m == pytest.approx(300, abs=5)
    assert result.closest_under_construction_m == pytest.approx(186, abs=5)
    assert result.closest_name == "Детский сад №2044"
    assert result.closest_unnamed is False


@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_school_preschool_wing_is_operational(mock_post):
    """Дошкольный корпус школы — настоящий работающий сад, не исключаем.

    «Школа № 1576. Корпус № 16» подписана как школа, но это дошкольное
    отделение (московская система образования). Пользователь должен видеть имя
    и понимать, что за объект, а не терять его из выдачи.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {
                "type": "way",
                "id": 2,
                "center": {"lat": _north_of_narvin(893), "lon": NARVIN_LON},
                "tags": {"amenity": "kindergarten", "name": "Школа № 1576. Корпус № 16"},
            }
        ]
    }
    mock_post.return_value = mock_response

    result = await fetch_poi(NARVIN_LAT, NARVIN_LON, POICategory.KINDERGARTEN, 2000)

    assert result.count_operational == 1
    assert result.closest_name == "Школа № 1576. Корпус № 16"
    assert result.count_under_construction == 0


@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_unnamed_and_private_objects_are_kept(mock_post):
    """Безымянный полигон и частная сеть остаются действующими, но помечены."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {
                "type": "way",
                "id": 3,
                "center": {"lat": _north_of_narvin(120), "lon": NARVIN_LON},
                "tags": {"amenity": "kindergarten", "barrier": "fence"},
            },
            {
                "type": "node",
                "id": 4,
                "lat": _north_of_narvin(248),
                "lon": NARVIN_LON,
                "tags": {
                    "amenity": "kindergarten",
                    "name": "Sun School",
                    "ownership": "private",
                },
            },
        ]
    }
    mock_post.return_value = mock_response

    result = await fetch_poi(NARVIN_LAT, NARVIN_LON, POICategory.KINDERGARTEN, 2000)

    assert result.count_operational == 2
    assert result.closest_distance_m == pytest.approx(120, abs=5)
    assert result.closest_unnamed is True
    assert result.closest_name is None


def test_legacy_cache_v1_read_without_crash_but_with_warning(narvin_ref_dir):
    """Старый формат кэша читается, но о его двусмысленности сообщаем.

    В v1 ``closest_distance_m`` считался по всем объектам, включая стройки, —
    молча использовать это число как «действующую» дистанцию значило бы ровно ту
    тихую деградацию, из-за которой шаг и появился.
    """
    (narvin_ref_dir / "poi_cache.json").write_text(
        json.dumps({"narvin": {"kindergarten": {"count": 19, "closest_distance_m": 186.4}}}),
        "utf-8",
    )
    warnings: list[str] = []

    candidates = build_candidate_shortlist(_kindergarten_criteria(200), warnings)

    assert candidates and candidates[0].id == "1165"
    assert any("refresh_poi" in w for w in warnings), warnings
