import pytest

from app.parsing.entity_match import match_entities
from app.reference.loader import clear_cache


@pytest.fixture(autouse=True)
def setup_reference_data():
    clear_cache()
    # We rely on the actual data from app/reference/*.json for testing
    # Rapidfuzz should match them easily


def test_entity_match_basic():
    text = "хочу двушку у метро Аэропорт Внуково"
    matches, warnings = match_entities(text)
    assert len(matches) == 1
    assert matches[0].type == "metro"
    assert matches[0].entity.name == "Аэропорт Внуково"
    assert not warnings


def test_entity_match_without_preposition():
    text = "хочу купить на Соколе"
    matches, _warnings = match_entities(text)
    assert len(matches) == 1
    assert matches[0].type == "metro"
    assert matches[0].entity.name == "Сокол"


def test_entity_match_multiple_words():
    text = "в ЖК Мичуринский парк"
    matches, _warnings = match_entities(text)
    assert len(matches) == 1
    assert matches[0].type == "complex"
    assert matches[0].entity.name == "Мичуринский парк"


def test_entity_match_ambiguous():
    # Similar names will trigger warning
    # e.g. "Кантимировская" typo for "Кантемировская"
    text = "м. Кантимировская"
    matches, _warnings = match_entities(text)
    assert len(matches) >= 1
    assert matches[0].type == "metro"
    assert matches[0].entity.name == "Кантемировская"


def test_entity_match_overlapping_windows():
    # Window 2: "Бульвар Рокоссовского"
    # Window 3: "метро Бульвар Рокоссовского"
    text = "у метро Бульвар Рокоссовского"
    matches, _warnings = match_entities(text)
    assert len(matches) == 1
    assert matches[0].entity.name == "Бульвар Рокоссовского"


def test_entity_match_multi():
    text = "ЖК Волжский парк и ЖК Бусиновский парк"
    matches, _warnings = match_entities(text)
    assert len(matches) == 2
    names = {m.entity.name for m in matches}
    assert "Волжский парк" in names
    assert "Бусиновский парк" in names


def test_entity_match_stopwords_not_ignoring_toponyms():
    text = "квартиру в Раменках или у метро Люберцы"
    matches, _warnings = match_entities(text)
    names = {m.entity.name for m in matches}
    assert "Раменки" in names
    assert any("Люберцы" in name for name in names)
