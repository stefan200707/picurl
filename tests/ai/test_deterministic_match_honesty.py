"""Д2: ``complexes_matched`` детерминированной ветки — факт, а не константа.

``EnrichmentResult.complexes_matched`` означает «сужение РЕАЛЬНО считалось», и
``merge_enrichment`` по нему решает, затирать ли ``criteria.complexes`` пустым
списком (``complexes_matched_empty``). Ветка ``from_deterministic`` выставляла
флаг безусловным ``True`` — то есть утверждала факт, которого не проверяла.

Опасен именно vacuous truth: если ни одно требование к ЖК не проверяемо
(категория POI не собрана в кэше — см. ``_collected_poi_categories``),
``resolve_known_facts`` объявляет совпавшими ВСЕХ кандидатов, и «сужение
посчитано» превращается в «вылить шорт-лист в blocks=». Это тот же класс, что
Milestone AI-21, только на другой ветке.

Ориентир правки — ``from_ai_unmatched`` (Г2): там флаг уже перестал быть
константой по ровно той же причине.
"""

from app.ai.enrichment import EnrichmentResult
from app.ai.schema import ComplexCandidate
from app.geo.candidates import resolve_known_facts
from app.parsing.schema import Criteria, POICategory, POIRequirement


def _candidate(cid: str, **poi: bool) -> ComplexCandidate:
    return ComplexCandidate(
        id=cid,
        name=f"ЖК {cid}",
        district=None,
        county=None,
        metro=[],
        is_center=None,
        known_poi=dict(poi),
    )


def test_from_deterministic_does_not_invent_the_fact():
    """Без явного признака факта флаг не выставляется — «не знаем» ≠ «считали»."""
    result = EnrichmentResult.from_deterministic({"matched_complex_ids": ["1", "2"]})

    assert result.complexes_matched is False


def test_from_deterministic_trusts_reported_fact():
    """Признак пришёл — флаг выставлен: легитимный ноль обязан пережить merge."""
    result = EnrichmentResult.from_deterministic(
        {"matched_complex_ids": [], "narrowing_applied": True}
    )

    assert result.complexes_matched is True


def test_known_facts_report_applied_narrowing():
    """Проверяемое POI-требование реально отсекает → сужение считалось."""
    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )
    candidates = [_candidate("1", school=True), _candidate("2", school=False)]

    known = resolve_known_facts(candidates, criteria)

    assert known["narrowing_applied"] is True
    assert known["matched_complex_ids"] == ["1"]


def test_known_facts_report_no_narrowing_for_uncollected_category():
    """Категории нет в POI-кэше → отсекать нечем, «совпали все» — vacuous truth.

    Флаг обязан это отличать: иначе ``merge_enrichment`` перепишет
    ``criteria.complexes`` шорт-листом целиком, выдав «мы посчитали» за факт.
    """
    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.CINEMA, raw_phrase="кинотеатр")]
    )
    candidates = [_candidate("1"), _candidate("2")]

    known = resolve_known_facts(candidates, criteria)

    assert known["matched_complex_ids"] == ["1", "2"], "предпосылка теста: совпали все"
    assert known["narrowing_applied"] is False
    assert EnrichmentResult.from_deterministic(known).complexes_matched is False
