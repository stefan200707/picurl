import pytest

from app.parsing.entity_match import build_choices


@pytest.fixture(autouse=True)
def clear_caches():
    build_choices.cache_clear()
    yield
    build_choices.cache_clear()
