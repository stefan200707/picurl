from app.parsing.parser import parse
from app.parsing.schema import Rooms, Sort


def test_parse_reference_example():
    """Тест на эталонном запросе из ТЗ."""
    text = "хочу 2-комнатную у метро Аэропорт Внуково, до 15 млн, с отделкой, сначала дешевле"
    result, warnings = parse(text)

    # Проверяем заполненные критерии
    assert result.rooms == [Rooms.TWO]
    assert result.price_max == 15000000
    assert result.finish is True
    assert result.sort == Sort.PRICE_ASC

    # Метро
    assert len(result.metro) == 1
    assert result.metro[0].name == "Аэропорт Внуково"

    # Не должно быть warnings на эталонном запросе
    assert warnings == []


def test_parse_unrecognized_chunk():
    """Тест на то, что нераспознанные куски не отбрасываются молча."""
    text = "1-комнатная квартира с выходом на крышу"
    result, warnings = parse(text)

    assert result.rooms == [Rooms.ONE]

    # "с выходом на крышу" должно попасть в warnings
    assert len(warnings) == 1
    assert "выходом на крышу" in warnings[0]
    assert "не удалось распознать" in warnings[0]


def test_parse_unsupported_secondary():
    """Тест на то, что неподдерживаемые термины (вторичка) попадают в warnings."""
    text = "куплю вторичку до 10 млн"
    result, warnings = parse(text)

    assert result.price_max == 10000000
    assert len(warnings) == 1
    assert "вторичк" in warnings[0].lower()
    assert "не удалось распознать" in warnings[0]


def test_parse_stop_words_ignored():
    """Тест на то, что остаточные стоп-слова не вызывают warnings."""
    # "ищу", "квартиру", "в", "и", "или" - стоп-слова
    text = "ищу квартиру студию или 1к"
    result, warnings = parse(text)

    assert result.rooms == [Rooms.STUDIO, Rooms.ONE]
    assert warnings == []
