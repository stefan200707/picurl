import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.memory import StructuredFact
from app.ai.promotion import analyze_option_aliases, promote, promote_aliases
from app.ai.schema import ComplexCandidate
from app.geo.candidates import resolve_known_facts
from app.geo.poi import POICategory
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
