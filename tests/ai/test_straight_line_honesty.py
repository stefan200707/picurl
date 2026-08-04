"""П5: дистанции измеряются ПО ПРЯМОЙ — и должны так и называться.

Роутинга в проекте нет: расстояние считает haversine по координатам, а для
полигонов (парк, лес, большая парковка) берётся центроид. Перевод «10 минут
пешком» → 800 м использует 80 м/мин, тоже по прямой, поэтому реальная ходьба
по улицам занимает примерно на 20-40% больше — отсечка систематически мягче
запрошенной.

Подкручивать сами числа коэффициентом извилистости не стали: это сдвинуло бы
все пороги QA разом ради оценки, точность которой всё равно неизвестна. Вместо
этого называем величину своим именем — пользователь дальше решает сам.
"""

from app.ai.enrichment import STRAIGHT_LINE_NOTE, _warn_poi_evidence
from app.ai.schema import ComplexCandidate
from app.geo.poi import POI_CACHE_SCHEMA_VERSION, POICategory
from app.parsing.schema import Criteria, POIRequirement


def _candidate() -> ComplexCandidate:
    return ComplexCandidate(
        id="1",
        name="Кантемировская 11",
        district=None,
        county=None,
        metro=[],
        is_center=None,
        known_poi={"school": True},
        poi_distances={"school": 88.0},
        poi_names={"school": "Школа № 104"},
        poi_unnamed={"school": False},
        poi_under_construction={"school": 0},
        poi_under_construction_m={"school": None},
        poi_schema_version={"school": POI_CACHE_SCHEMA_VERSION},
    )


def test_poi_evidence_says_distance_is_straight_line() -> None:
    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школы")]
    )
    warnings: list[str] = []

    _warn_poi_evidence(criteria, [_candidate()], ["1"], warnings)

    assert warnings, "справка о POI обязана быть"
    assert STRAIGHT_LINE_NOTE in warnings[0], warnings[0]
    assert "88 м" in warnings[0]
