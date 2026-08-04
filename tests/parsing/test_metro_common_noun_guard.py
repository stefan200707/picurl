"""П3: станция-омоним бытового слова требует явного маркера метро.

«рядом университет» — это пожелание «чтобы поблизости был ВУЗ», а не фильтр по
станции «Университет». Fuzzy-матч по metro.json перехватывал такое слово и
МОЛЧА подменял смысл: ни warning'а, ни следа в option_candidates — единственный
известный случай, где потеря не просто тихая, а с искажением.

Приём тот же, что у «сада» в rules/poi.py, но с другой стороны: там бытовое
слово защищали от топонима, здесь топоним от бытового слова.
"""

import pytest

from app.parsing.parser import parse


@pytest.mark.parametrize("text", ["рядом университет", "рядом аэропорт", "рядом технопарк"])
def test_bare_common_noun_does_not_become_metro_filter(text: str) -> None:
    result = parse(text)

    assert [m.name for m in result.criteria.metro] == []
    # Фраза обязана быть либо распознана по существу (для «университет» это
    # POI-категория UNIVERSITY), либо видимо потеряна — но не превращена молча
    # в фильтр по станции.
    assert result.criteria.poi_requirements or result.warnings


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("рядом метро Университет", "Университет"),
        ("однушку у метро Аэропорт", "Аэропорт"),
        ("м. Технопарк", "Технопарк"),
    ],
)
def test_explicit_metro_marker_still_matches(text: str, expected: str) -> None:
    result = parse(text)

    assert expected in [m.name for m in result.criteria.metro], result.criteria.metro


def test_qualified_landmark_is_not_hijacked_by_station() -> None:
    """«Финансовому университету» — ориентир, а не станция «Университет»."""
    result = parse("однушку ближайшую к Финансовому университету")

    assert "Университет" not in [m.name for m in result.criteria.metro]
