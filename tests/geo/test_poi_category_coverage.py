"""П2: непокрытая кэшем POI-категория обязана говорить, а не молча всё обнулять.

``resolve_known_facts`` требует наличия категории в ``known_poi`` кандидата.
Категории, которой в кэше нет ни у одного ЖК (``OTHER``, любая будущая),
не удовлетворяет НИКТО — шорт-лист схлопывается в ноль без единого слова.
Снаружи это неотличимо от честного «подходящих ЖК нет», что прямо нарушает
инвариант 1.
"""

import pytest

from app.ai.enrichment import sanitize_poi_resolution
from app.ai.schema import FreeTextCriteriaAnswer, POIMatch
from app.geo.candidates import build_candidate_shortlist
from app.geo.poi import POI_CATEGORY_NOT_CACHED_WARNING, POICategory
from app.parsing.schema import Criteria, POIRequirement


def test_sanitize_poi_rejects_other_category() -> None:
    """`other` — валидный член энума, но кэша под него нет и не будет.

    Приняв его, мы получили бы требование, которое молча отсеивает всех.
    Честнее оставить фразу в warnings.
    """
    answer = FreeTextCriteriaAnswer(
        poi=[POIMatch(phrase="рядом бургер кинг", category=POICategory.OTHER)]
    )

    assert sanitize_poi_resolution(answer, ["рядом бургер кинг"]) == []


@pytest.mark.parametrize("category", [POICategory.OTHER])
def test_shortlist_warns_when_category_absent_from_cache(category: POICategory) -> None:
    criteria = Criteria(
        poi_requirements=[POIRequirement(category=category, raw_phrase="бургер кинг")]
    )
    warnings: list[str] = []

    build_candidate_shortlist(criteria, warnings)

    assert POI_CATEGORY_NOT_CACHED_WARNING.format(category=category.value) in warnings, warnings


def test_shortlist_silent_for_cached_category() -> None:
    """Контраст: покрытая категория предупреждений не порождает."""
    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )
    warnings: list[str] = []

    build_candidate_shortlist(criteria, warnings)

    expected = POI_CATEGORY_NOT_CACHED_WARNING.format(category=POICategory.SCHOOL.value)
    assert expected not in warnings, warnings


def test_globally_uncached_category_does_not_filter_anyone_out() -> None:
    """Категория, которой нет в кэше НИ У КОГО, не должна отсеивать всех.

    Различаем два разных «данных нет»:
    - у конкретного ЖК (нет координат → нет записи) — близость недоказуема,
      кандидат требование не проходит, это правильно;
    - у всех сразу (категория в кэш ещё не собрана) — это пробел НАШИХ данных,
      а не факт о ЖК. Отсеяв по нему всех, мы выдали бы «подходящих нет» там,
      где на самом деле «мы не проверяли» — и предупреждение из П2 стало бы
      единственным следом, тонущим среди прочих.
    """
    from app.geo.candidates import build_candidate_shortlist, resolve_known_facts

    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.OTHER, raw_phrase="бургер кинг")]
    )
    candidates = build_candidate_shortlist(criteria, [])
    assert candidates, "шорт-лист не должен быть пуст — тест потерял смысл"

    known = resolve_known_facts(candidates, criteria)

    assert known["matched_complex_ids"], "непроверяемое требование не должно обнулять выдачу"


def test_per_candidate_gap_still_filters_that_candidate() -> None:
    """Контраст: если категория в кэше ЕСТЬ, ЖК без неё требование не проходит."""
    from app.ai.schema import ComplexCandidate
    from app.geo.candidates import resolve_known_facts

    criteria = Criteria(
        poi_requirements=[POIRequirement(category=POICategory.SCHOOL, raw_phrase="школа")]
    )
    with_school = ComplexCandidate(
        id="1",
        name="A",
        district=None,
        county=None,
        metro=[],
        is_center=None,
        known_poi={"school": True},
    )
    without_data = ComplexCandidate(
        id="2",
        name="B",
        district=None,
        county=None,
        metro=[],
        is_center=None,
        known_poi={},
    )

    known = resolve_known_facts([with_school, without_data], criteria)

    assert known["matched_complex_ids"] == ["1"]
