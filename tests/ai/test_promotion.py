import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.memory import StructuredFact
from app.ai.promotion import promote
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
