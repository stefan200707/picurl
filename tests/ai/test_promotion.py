import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.memory import StructuredFact
from app.ai.promotion import analyze_option_aliases, promote, promote_aliases
from app.ai.schema import ComplexCandidate
from app.geo.candidates import _has_stale_poi_entries, resolve_known_facts
from app.geo.poi import POI_CACHE_SCHEMA_VERSION, POICategory
from app.geo.refresh_poi import _needs_refresh
from app.parsing.schema import Criteria, POIRequirement


@pytest.fixture
def temp_data_dir(tmp_path):
    counties_path = tmp_path / "counties.json"
    districts_path = tmp_path / "districts.json"
    complexes_path = tmp_path / "complexes.json"
    poi_cache_path = tmp_path / "poi_cache.json"

    counties_path.write_text(json.dumps([{"name": "ЦАО", "slug": "cao", "id": "1"}]))
    districts_path.write_text(json.dumps([{"name": "Тверской", "slug": "tverskoy", "id": "10"}]))
    complexes_path.write_text(json.dumps([{"name": "ЖК Центр", "slug": "center", "id": "100"}]))
    poi_cache_path.write_text("{}")

    yield tmp_path


@pytest.mark.asyncio
async def test_promote_facts(temp_data_dir):
    with patch("app.ai.promotion.DATA_DIR", temp_data_dir):
        pool = AsyncMock()

        facts = [
            StructuredFact(
                id=1,
                subject_type="county",
                subject_id="1",
                fact_type="is_center",
                fact_value={"is_center": True},
                source="ai_inference",
                confidence=0.9,
                observed_count=4,
                first_seen_at=datetime.now(),
                last_confirmed_at=datetime.now(),
            ),
            StructuredFact(
                id=2,
                subject_type="complex",
                subject_id="100",
                fact_type="poi_school",
                fact_value={"present": True},
                source="ai_inference",
                confidence=0.9,
                observed_count=4,
                first_seen_at=datetime.now(),
                last_confirmed_at=datetime.now(),
            ),
            StructuredFact(
                id=3,
                subject_type="district",
                subject_id="99",
                fact_type="is_center",
                fact_value={"is_center": True},
                source="ai_inference",
                confidence=0.9,
                observed_count=4,
                first_seen_at=datetime.now(),
                last_confirmed_at=datetime.now(),
            ),
        ]

        report = await promote(pool, facts)
        assert report.promoted_count == 2
        assert report.ignored_count == 1

        counties = json.loads((temp_data_dir / "counties.json").read_text())
        assert counties[0]["is_center"] is True

        poi_cache = json.loads((temp_data_dir / "poi_cache.json").read_text())
        assert poi_cache["center"]["school"]["count"] == 1

        # Idempotency
        report2 = await promote(pool, facts)
        assert report2.promoted_count == 2


def _poi_fact(fact_id: int, complex_id: str, fact_type: str, present: bool) -> StructuredFact:
    return StructuredFact(
        id=fact_id,
        subject_type="complex",
        subject_id=complex_id,
        fact_type=fact_type,
        fact_value={"present": present},
        source="ai_inference",
        confidence=0.9,
        observed_count=5,
        first_seen_at=datetime.now(),
        last_confirmed_at=datetime.now(),
    )


@pytest.mark.asyncio
async def test_promote_new_poi_entry_is_v2(temp_data_dir):
    """НОВАЯ запись (измерения не было) — полноценная v2-заглушка без дистанции."""
    with patch("app.ai.promotion.DATA_DIR", temp_data_dir):
        await promote(AsyncMock(), [_poi_fact(2, "100", "poi_school", True)])

        entry = json.loads((temp_data_dir / "poi_cache.json").read_text())["center"]["school"]
        assert entry["count_operational"] == 1
        assert entry["count_under_construction"] == 0
        assert entry["schema_version"] == POI_CACHE_SCHEMA_VERSION
        # Дистанцию промоушен не выдумывает: неизвестна = None.
        assert entry["closest_distance_m"] is None


@pytest.mark.asyncio
async def test_promote_does_not_stamp_v2_on_existing_v1_entry(temp_data_dir):
    """v1-запись остаётся v1: иначе слепнут ОБА предохранителя устаревшей схемы.

    В v1 ``closest_distance_m`` считался по всем объектам OSM, включая стройки
    (живой случай: «сад в 186 м» = котлован). Пометка такой записи как v2
    превращала недостоверное число в «дистанцию до действующего объекта»:
    stale-warning больше не выдавался, а ``refresh_poi`` перестал её добирать.
    """
    with patch("app.ai.promotion.DATA_DIR", temp_data_dir):
        (temp_data_dir / "poi_cache.json").write_text(
            json.dumps({"center": {"school": {"count": 4, "closest_distance_m": 186.0}}}),
            encoding="utf-8",
        )

        await promote(AsyncMock(), [_poi_fact(2, "100", "poi_school", True)])

        entry = json.loads((temp_data_dir / "poi_cache.json").read_text())["center"]["school"]
        assert entry.get("schema_version", 1) == 1
        assert entry["closest_distance_m"] == 186.0
        assert _has_stale_poi_entries({"center": {"school": entry}}) is True
        assert _needs_refresh(entry, force=False) is True


@pytest.mark.asyncio
async def test_promote_does_not_overwrite_measured_count(temp_data_dir):
    """Догадка ИИ не затирает ИЗМЕРЕНИЕ (инвариант 3) и не рвёт запись изнутри.

    Раньше ``present=False`` записывал ``count_operational=0`` рядом с живыми
    ``closest_distance_m``/``closest_name``: запись противоречила себе, а
    «садов нет» получалось догадкой против измерения.
    """
    measured = {
        "count": 7,
        "closest_distance_m": 300.0,
        "closest_name": "Детский сад №2044",
        "closest_unnamed": False,
        "count_operational": 7,
        "count_under_construction": 0,
        "closest_under_construction_m": None,
        "schema_version": POI_CACHE_SCHEMA_VERSION,
    }
    with patch("app.ai.promotion.DATA_DIR", temp_data_dir):
        (temp_data_dir / "poi_cache.json").write_text(
            json.dumps({"center": {"kindergarten": measured}}, ensure_ascii=False),
            encoding="utf-8",
        )

        report = await promote(AsyncMock(), [_poi_fact(2, "100", "poi_kindergarten", False)])

        entry = json.loads((temp_data_dir / "poi_cache.json").read_text())["center"]["kindergarten"]
        assert entry == measured
        # Факт не применён — значит и «промоутированным» его считать нельзя.
        assert report.promoted_count == 0
        assert report.ignored_count == 1


@pytest.mark.asyncio
async def test_promote_poi_fact_agreeing_with_measurement_is_idempotent(temp_data_dir):
    """Согласный с измерением факт считается применённым (повторный прогон стабилен)."""
    with patch("app.ai.promotion.DATA_DIR", temp_data_dir):
        (temp_data_dir / "poi_cache.json").write_text(
            json.dumps(
                {
                    "center": {
                        "school": {
                            "count": 3,
                            "count_operational": 3,
                            "count_under_construction": 0,
                            "closest_distance_m": 250.0,
                            "schema_version": POI_CACHE_SCHEMA_VERSION,
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        report = await promote(AsyncMock(), [_poi_fact(2, "100", "poi_school", True)])

        assert report.promoted_count == 1
        entry = json.loads((temp_data_dir / "poi_cache.json").read_text())["center"]["school"]
        assert entry["count_operational"] == 3
        assert entry["closest_distance_m"] == 250.0


@pytest.fixture
def temp_alias_dir(tmp_path):
    """Каталог со справочниками опций для промоушена алиасов."""
    (tmp_path / "option_groups.json").write_text(
        json.dumps(
            [
                {"name": "Два и более санузла", "slug": "manybathrooms", "aliases": ["2 санузла"]},
                {"name": "Сквозной санузел", "slug": "throughbathroom", "aliases": []},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "options.json").write_text(
        json.dumps(
            [{"name": "Вид на город", "slug": "vidNaGorod", "aliases": []}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    yield tmp_path


def _alias_fact(fact_id, slug, phrase, count, subject_type="option_group", conf=0.9):
    return StructuredFact(
        id=fact_id,
        subject_type=subject_type,
        subject_id=slug,
        fact_type="option_alias",
        fact_value={"phrase": phrase},
        source="ai_inference",
        confidence=conf,
        observed_count=count,
        first_seen_at=datetime.now(),
        last_confirmed_at=datetime.now(),
    )


@pytest.mark.asyncio
async def test_promote_alias_consistent(temp_alias_dir):
    """Согласованные наблюдения одной фразы → промоутится в alias."""
    with patch("app.ai.promotion.DATA_DIR", temp_alias_dir):
        pool = AsyncMock()
        facts = [
            _alias_fact(1, "manybathrooms", "отдельный санузел", 3),
            _alias_fact(2, "manybathrooms", "отдельный санузел", 4),
        ]

        report = await promote_aliases(pool, facts)

        assert ("отдельный санузел", "manybathrooms") in report.promoted
        assert report.ambiguous == []

        groups = json.loads((temp_alias_dir / "option_groups.json").read_text())
        entry = next(g for g in groups if g["slug"] == "manybathrooms")
        assert "отдельный санузел" in entry["aliases"]
        # Кураторский алиас не затёрт.
        assert "2 санузла" in entry["aliases"]


@pytest.mark.asyncio
async def test_promote_alias_divergent_is_ambiguous(temp_alias_dir):
    """Расходящиеся наблюдения фразы на разные slug → не промоутится никогда,
    попадает в отчёт «неоднозначные»."""
    with patch("app.ai.promotion.DATA_DIR", temp_alias_dir):
        pool = AsyncMock()
        # Гигантский перевес одного slug всё равно не должен промоутить фразу —
        # любое расхождение при дефолтном пороге консистентности = конфликт.
        facts = [
            _alias_fact(1, "manybathrooms", "отдельный санузел", 1000),
            _alias_fact(2, "throughbathroom", "отдельный санузел", 1),
        ]

        report = await promote_aliases(pool, facts)

        assert report.promoted == []
        assert any(a.phrase == "отдельный санузел" for a in report.ambiguous)
        ambiguous = next(a for a in report.ambiguous if a.phrase == "отдельный санузел")
        assert set(ambiguous.slug_counts) == {"manybathrooms", "throughbathroom"}

        # Ничего не записано в справочник.
        groups = json.loads((temp_alias_dir / "option_groups.json").read_text())
        entry = next(g for g in groups if g["slug"] == "manybathrooms")
        assert "отдельный санузел" not in entry["aliases"]


def test_analyze_option_aliases_below_threshold_stays_on_ai():
    """Согласованная, но редкая фраза не промоутится (тихо остаётся на ИИ)."""
    facts = [_alias_fact(1, "manybathrooms", "свой санузел", 2)]
    promotable, ambiguous = analyze_option_aliases(
        facts, min_observations=5, min_confidence=0.8, min_consistency=1.0
    )
    assert promotable == []
    assert ambiguous == []


@pytest.mark.asyncio
async def test_resolve_known_facts_uses_cache(temp_data_dir):
    with (
        patch("app.geo.candidates.DATA_DIR", temp_data_dir),
        patch("app.reference.loader.DATA_DIR", temp_data_dir),
    ):
        temp_data_dir.joinpath("complexes.json").write_text(
            json.dumps([{"name": "ЖК", "slug": "slug1", "id": "1"}])
        )
        temp_data_dir.joinpath("poi_cache.json").write_text(
            json.dumps({"slug1": {"school": {"count": 1, "closest_distance_m": 100}}})
        )
        # Also need metro.json, counties.json, districts.json, options etc. for load_all to work!
        temp_data_dir.joinpath("metro.json").write_text("[]")
        temp_data_dir.joinpath("counties.json").write_text("[]")
        temp_data_dir.joinpath("districts.json").write_text("[]")
        temp_data_dir.joinpath("benefits.json").write_text("[]")
        temp_data_dir.joinpath("option_groups.json").write_text("[]")
        temp_data_dir.joinpath("options.json").write_text("[]")
        temp_data_dir.joinpath("landmarks.json").write_text("[]")

        criteria = Criteria(
            poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
        )
        candidates = [
            ComplexCandidate(
                id="1",
                name="ЖК",
                district=None,
                county=None,
                metro=[],
                is_center=None,
                known_poi={"school": True},
            )
        ]

        known = resolve_known_facts(candidates, criteria)
        assert "1" in known["matched_complex_ids"]
        assert known["poi_findings"]["1"]["school"] is True
