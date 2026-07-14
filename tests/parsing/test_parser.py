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


def test_parse_unsupported_secondary() -> None:
    text = "ищу вторичку в ЗАО"
    result = parse(text)
    assert result.criteria.counties[0].slug == "zao"
    assert len(result.warnings) == 1
    assert "фильтр не поддерживается сайтом ПИК" in result.warnings[0]


def test_parse_stop_words_ignored():
    """Тест на то, что остаточные стоп-слова не вызывают warnings."""
    # "ищу", "квартиру", "в", "и", "или" - стоп-слова
    text = "ищу квартиру студию или 1к"
    result, warnings = parse(text)

    assert result.rooms == [Rooms.STUDIO, Rooms.ONE]
    assert warnings == []


def test_parse_garbage_cleanup():
    """Тест на то, что ошметки вроде 'с' и ',' не ломают матчинг и не попадают в warning."""
    # "с отделкой" вырезается. Остается "с тёплым полом, ".
    # Если мы не чистим мусор, "с" и "," мешают скользящему окну.
    text = "с тёплым полом, с отделкой"
    result, warnings = parse(text)

    assert result.finish is True
    assert len(result.option_groups) == 1
    assert result.option_groups[0] == "teplyPol"  # Проверим, что тёплый пол сматчился
    # Одиночные предлоги не должны генерить варнинги (если не считаются значимыми)
    assert "«с»" not in " ".join(warnings)
