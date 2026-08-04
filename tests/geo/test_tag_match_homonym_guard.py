"""Д3/Д4: омонимия названий локаций и её влияние на шорт-лист.

Живой репродьюсер («…в районе метро Раменки (или Ломоносовский проспект)…»)
показал связку из двух дефектов ОДНОГО механизма — тег-матча по ИМЕНИ локации
(``app.geo.candidates._location_filter`` + фильтр кандидатов):

* **Д3.** Имя района в справочнике не уникально: единственный ЖК с
  ``district="Ломоносовский"`` — питерский «Таллинский парк» (59.81/30.10), а
  пользователь спрашивал про московский Ломоносовский район. Совпадение
  нормализованных строк выдавалось за совпадение места.
* **Д4.** Непустой (пусть и мусорный) тег-матч закрывал ветку ранжирования по
  дистанции — 8 реальных московских ЖК рядом с названными станциями в шорт-лист
  не попадали вовсе.

Проверяем оба на синтетическом справочнике: реальные координаты сюда не
подставляем, чтобы тест не зависел от очередного ``refresh``.
"""

import json

import pytest

from app.geo.candidates import TAG_MATCH_ANCHOR_RADIUS_M, build_candidate_shortlist
from app.parsing.schema import Criteria, MatchedEntity

#: Синтетическая московская станция (координаты примерно как у Раменок).
_STATION_LAT = 55.7000
_STATION_LON = 37.4900


@pytest.fixture
def homonym_ref_dir(tmp_path, monkeypatch):
    """Справочник с ОМОНИМОМ района: одно имя, два города.

    «Спорный» носят два ЖК — один в 630 км от станции запроса (другой город),
    другой в паре километров. Плюс два ЖК вообще без привязки к району: они
    нужны, чтобы у ветки ранжирования по дистанции было что показать.
    """
    (tmp_path / "complexes.json").write_text(
        json.dumps(
            [
                # Тёзка из другого города: имя района совпадает, место — нет.
                {
                    "name": "Иногородний тёзка",
                    "slug": "far",
                    "id": "1",
                    "district": "Спорный",
                    "lat": 59.8096,
                    "lon": 30.0983,
                },
                # Ближний ЖК без района: тег-матч его не берёт, дистанция — берёт.
                {"name": "Рядом", "slug": "near", "id": "2", "lat": 55.7050, "lon": 37.4950},
                # Чуть дальше, но всё ещё в городе.
                {"name": "Подальше", "slug": "mid", "id": "3", "lat": 55.7520, "lon": 37.6175},
            ],
            ensure_ascii=False,
        ),
        "utf-8",
    )
    (tmp_path / "metro.json").write_text(
        json.dumps(
            [{"name": "Якорная", "lat": _STATION_LAT, "lon": _STATION_LON}],
            ensure_ascii=False,
        ),
        "utf-8",
    )
    for name in ("counties", "districts", "benefits", "option_groups", "options", "landmarks"):
        (tmp_path / f"{name}.json").write_text("[]", "utf-8")
    (tmp_path / "poi_cache.json").write_text("{}", "utf-8")

    monkeypatch.setattr("app.reference.loader.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.geo.candidates.DATA_DIR", tmp_path)
    from app.reference.loader import clear_cache

    clear_cache()
    yield tmp_path
    clear_cache()


def _criteria_with_anchor() -> Criteria:
    """Запрос «район Спорный у станции Якорная» — есть и имя, и якорь-координаты."""
    return Criteria(
        metro=[MatchedEntity(name="Якорная")],
        districts=[MatchedEntity(name="Спорный")],
    )


# --------------------------------------------------------------------------
# Д3: тег-матч по имени принимается только для географически правдоподобных ЖК
# --------------------------------------------------------------------------


def test_tag_match_rejects_homonym_from_another_region(homonym_ref_dir):
    """ЖК-тёзка в 630 км от запрошенной станции совпадением не считается."""
    warnings: list[str] = []

    names = {c.name for c in build_candidate_shortlist(_criteria_with_anchor(), warnings)}

    assert "Иногородний тёзка" not in names, "омоним другого региона попал в шорт-лист"
    # Инвариант 1: отбросили — сказали (и назвали ЖК, чтобы факт был проверяем).
    assert any("Иногородний тёзка" in w for w in warnings), warnings


def test_tag_match_keeps_plausible_complex(homonym_ref_dir, tmp_path):
    """Контраст: тёзка В ПРЕДЕЛАХ радиуса остаётся, и никакой деградации нет."""
    (tmp_path / "complexes.json").write_text(
        json.dumps(
            [
                {
                    "name": "Свой",
                    "slug": "own",
                    "id": "1",
                    "district": "Спорный",
                    "lat": 55.7050,
                    "lon": 37.4950,
                },
                {"name": "Другой", "slug": "other", "id": "2", "lat": 55.7520, "lon": 37.6175},
            ],
            ensure_ascii=False,
        ),
        "utf-8",
    )
    from app.reference.loader import clear_cache

    clear_cache()
    warnings: list[str] = []

    names = {c.name for c in build_candidate_shortlist(_criteria_with_anchor(), warnings)}

    assert names == {"Свой"}
    assert warnings == []


def test_tag_match_guard_inactive_without_anchor(homonym_ref_dir):
    """Якоря нет (метро в запросе не названо) — судить не по чему, тег в силе.

    Координаты есть только у метро; выдумывать центроид района запрещает
    инвариант 3. Поэтому запрос без станций работает ровно как раньше — иначе
    правка молча выкинула бы весь неМосковский портфель ПИК.
    """
    criteria = Criteria(districts=[MatchedEntity(name="Спорный")])
    warnings: list[str] = []

    names = {c.name for c in build_candidate_shortlist(criteria, warnings)}

    assert names == {"Иногородний тёзка"}
    assert warnings == []


def test_tag_match_anchor_radius_is_region_scale():
    """Порог — «тот же регион», а не «тот же квартал» (docs/thresholds-rationale.md).

    По фактическим данным справочника: самый дальний московский ЖК от самой
    дальней станции — 80.7 км, ближайший иногородний — 228.4 км. Порог обязан
    лежать между ними с запасом.
    """
    assert 80_700.0 < TAG_MATCH_ANCHOR_RADIUS_M < 228_400.0


# --------------------------------------------------------------------------
# Д4: непустой тег-матч не должен отключать ранжирование по дистанции
# --------------------------------------------------------------------------


def test_unconfirmed_tag_match_enables_distance_ranking(homonym_ref_dir):
    """Тег-матч не подтвердил геометрию → включается ранжирование по дистанции.

    До правки условие звучало как «список пуст», и единственный мусорный
    кандидат блокировал ветку целиком: реальные ЖК рядом со станцией в шорт-лист
    не попадали.
    """
    warnings: list[str] = []

    candidates = build_candidate_shortlist(_criteria_with_anchor(), warnings)

    assert [c.name for c in candidates][:2] == ["Рядом", "Подальше"]
    # Инвариант 13: усечение после сортировки, ближайший — первым.
    distances = [c.distance_to_location_m for c in candidates if c.distance_to_location_m]
    assert distances == sorted(distances)
    assert any("ближайшие по расстоянию" in w for w in warnings), warnings
