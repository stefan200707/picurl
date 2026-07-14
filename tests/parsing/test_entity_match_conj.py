from app.parsing.entity_match import match_entities
from app.reference.loader import clear_cache


def test_entity_match_conjunction():
    clear_cache()
    text = "хочу с видом на воду и город"
    matches, _warnings = match_entities(text)
    names = {m.entity.name for m in matches}
    assert "Вид на воду" in names
    assert "Вид на город" in names


test_entity_match_conjunction()
print("Success")
