from unittest.mock import patch

from app.parsing.entity_match import match_entities
from app.reference.loader import RefEntry, ReferenceData


def mock_load_all():
    return ReferenceData(
        metro=[],
        counties=[RefEntry(name="Западный округ", slug="county", aliases=["западный"])],
        districts=[RefEntry(name="Западный район", slug="district", aliases=["западный"])],
        complexes=[RefEntry(name="Западный", slug="complex", aliases=[])],
        options=[],
        option_groups=[],
        benefits=[],
    )


@patch("app.parsing.entity_match.load_all", side_effect=mock_load_all)
def test_ambiguity_county_priority(mock_fn):
    # Without context, County > District > Complex
    matches, _warnings = match_entities("ищу квартиру: Западный")
    assert len(matches) == 1
    assert matches[0].type == "county"


@patch("app.parsing.entity_match.load_all", side_effect=mock_load_all)
def test_ambiguity_preposition_na(mock_fn):
    # "на" favors county
    matches, _warnings = match_entities("на Западный")
    assert len(matches) == 1
    assert matches[0].type == "county"


@patch("app.parsing.entity_match.load_all", side_effect=mock_load_all)
def test_ambiguity_preposition_v(mock_fn):
    # "в" favors complex
    matches, _warnings = match_entities("в Западный")
    assert len(matches) == 1
    assert matches[0].type == "complex"


@patch("app.parsing.entity_match.load_all", side_effect=mock_load_all)
def test_ambiguity_context_jk(mock_fn):
    # "ЖК" strongly favors complex
    matches, _warnings = match_entities("в жк Западный")
    assert len(matches) == 1
    assert matches[0].type == "complex"
