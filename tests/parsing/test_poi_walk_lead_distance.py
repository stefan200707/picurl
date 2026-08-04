"""O2: слово способа передвижения ПЕРЕД числом не должно съедать дистанцию.

``_WALK_TAIL`` допускал «пешком/идти/ходьбы» только ПОСЛЕ величины («10 минут
пешком»). Живая формулировка «до школы идти 12 минут» ставит его перед числом,
и регекс дистанции не начинался вовсе: величина отваливалась в warnings, а
POI-требование оставалось без ``max_distance_m``.

На маршрут запроса это НЕ влияет, вопреки первоначальному предположению:
дистанции до POI лежат в кэше (``poi_distances``), поэтому требование с
отсечкой разрешимо детерминированно ровно так же, как и без неё —
``fully_resolved`` остаётся True, и гейт 2 уводит запрос мимо ИИ в обоих
случаях. Цена дефекта чисто в выдаче: отсечка не применялась, и в ссылку
попадали ЖК, до школы от которых дальше запрошенного.
"""

import pytest

from app.parsing.parser import parse
from app.parsing.rules.core import WALK_METERS_PER_MINUTE


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("до школы идти 12 минут", "school"),
        ("до сада идти 12 минут", "kindergarten"),
        ("до поликлиники пешком 12 минут", "medical"),
        ("до магазина ходьбы 12 минут", "shop"),
    ],
)
def test_walk_word_before_number_keeps_distance(text: str, category: str) -> None:
    result = parse(text)

    reqs = result.criteria.poi_requirements
    assert [r.category.value for r in reqs] == [category]
    assert reqs[0].max_distance_m == 12 * WALK_METERS_PER_MINUTE
    assert result.warnings == []


def test_walk_word_after_number_still_works() -> None:
    """Контраст: прежняя форма не должна пострадать."""
    result = parse("до школы 12 минут пешком")

    reqs = result.criteria.poi_requirements
    assert reqs[0].max_distance_m == 12 * WALK_METERS_PER_MINUTE
    assert result.warnings == []


def test_walk_word_with_explicit_marker_still_works() -> None:
    """И форма с «до» перед числом — она работала и раньше."""
    result = parse("до школы идти до 12 минут")

    assert result.criteria.poi_requirements[0].max_distance_m == 12 * WALK_METERS_PER_MINUTE
    assert result.warnings == []


def test_metro_time_is_not_stolen_by_poi_rule() -> None:
    """Guard из rules/time.py обязан устоять: «до метро 15 минут» — timeOnFoot.

    Историческая причина guard'а: «до садика 12 минут пешком» выставляло
    реальный timeOnFoot=12, и api.pik.ru отдавал 0 квартир вместо 31.
    """
    result = parse("до метро идти 15 минут, до школы идти 12 минут")

    assert result.criteria.time_on_foot == 15
    school = [r for r in result.criteria.poi_requirements if r.category.value == "school"]
    assert school and school[0].max_distance_m == 12 * WALK_METERS_PER_MINUTE
