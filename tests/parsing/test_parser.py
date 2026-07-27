import pytest

from app.parsing.parser import parse
from app.parsing.schema import Finish, Rooms, Sort


def test_parse_reference_example():
    """Тест на эталонном запросе из ТЗ."""
    text = "хочу 2-комнатную у метро Аэропорт Внуково, до 15 млн, с отделкой, сначала дешевле"
    result, warnings = parse(text)

    # Проверяем заполненные критерии
    assert result.rooms == [Rooms.TWO]
    assert result.price_max == 15000000
    assert result.finish == [1]
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
    assert "не поддерживается pik.ru" in result.warnings[0]


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

    assert result.finish == [1]
    assert len(result.option_groups) == 1
    assert result.option_groups[0] == "teplyPol"  # Проверим, что тёплый пол сматчился
    # Одиночные предлоги не должны генерить варнинги (если не считаются значимыми)
    assert "«с»" not in " ".join(warnings)


class TestSingleCharHomoglyphStopwords:
    """Однобуквенный огрызок, совпадающий со стоп-словом ПОСЛЕ гомоглиф-
    нормализации (латинская 'c' — визуальный дубль кириллической «с»), не
    считается значимым ни в одном алфавите (Milestone AI-20, Фикс 4)."""

    def test_latin_c_homoglyph_not_significant(self):
        from app.parsing.parser import _is_significant

        latin_c = "c"
        assert _is_significant(latin_c) is False

    def test_cyrillic_stopword_still_not_significant(self):
        from app.parsing.parser import _is_significant

        assert _is_significant("с") is False


class TestPureProximityChunkNotOptionCandidate:
    """Фрагмент, целиком состоящий из маркеров близости/служебных слов/слова
    «метро», не должен становиться кандидатом в опции — раньше каждый такой
    геохвост порождал лишний вызов ИИ-резолвера опций (Milestone AI-20,
    Фикс 4).
    """

    @pytest.mark.parametrize(
        "text",
        [
            "недалеко от метро",
            "возле метро",
            "неподалеку от метро",
            "неподалёку от метро",
            "поблизости от метро",
            "недалеко от района",
            "возле округа",
        ],
    )
    def test_looks_like_option_rejects_pure_proximity_chunk(self, text: str):
        from app.parsing.parser import _looks_like_option

        assert _looks_like_option(text) is False


def test_control_case_new_kindergarten_near_metro_kommunarka():
    """Регрессия контрольного кейса Milestone AI-20.

    Раньше давало: warnings ["«c»: не удалось распознать…",
    "«недалеко от метро»: не удалось распознать…"], лишний district
    «Коммунарка» вдобавок к метро «Коммунарка», и «недалеко от метро» в
    option_candidates (лишний вызов ИИ). Причины — гомоглиф латинской 'c'
    (Фикс 2), неполный список маркеров близости (Фикс 1+3) и молчаливое
    добавление одноимённой сущности сразу двух типов (Фикс 5).
    """
    latin_c = "c"
    text = f"Нужна двушка {latin_c} новым детсадом недалеко от метро Коммунарка"
    result = parse(text)

    assert result.warnings == []
    assert result.option_candidates == []
    assert result.criteria.rooms == [Rooms.TWO]
    assert [m.name for m in result.criteria.metro] == ["Коммунарка"]
    assert result.criteria.districts == []
    assert len(result.criteria.poi_requirements) == 1
    assert result.criteria.poi_requirements[0].only_new is True


@pytest.mark.parametrize("text", ["ветки", "линии", "ветка метро"])
def test_looks_like_option_rejects_line_carrier_words(text: str):
    """«ветка»/«линия» — слова-носители гео-конструкций, не кандидаты в опции
    (Milestone AI-21): огрызок «ветки» порождал паразитный вызов ИИ."""
    from app.parsing.parser import _looks_like_option

    assert _looks_like_option(text) is False


def test_floor_with_etazhnost_prefix_leaves_no_residual():
    """«этажностью» входит в спан правила этажа, а не оседает ложным warning'ом.

    Факт распознан (floor_min/floor_max выставлены) — сообщать пользователю «не
    удалось распознать» про слово, которое как раз и было распознано, значит
    врать (инвариант 1: отбрасывается значение, а не текст).
    """
    result = parse("этажностью от 9 до 16 этажа")

    assert result.criteria.floor_min == 9
    assert result.criteria.floor_max == 16
    assert result.warnings == []


def test_modal_and_demonstrative_filler_is_not_a_warning():
    """Модальность «должны быть» и указательное «этих» — служебный шум, не факт.

    Оба класса слов не несут фильтрующего смысла ни в каком контексте, поэтому
    лечатся STOP_WORDS, а не расширением POI-спана влево (спан их не закроет —
    они встречаются и перед ценой/отделкой/метро).
    """
    result = parse(
        "рядом должны быть детские сады, чтобы до этих детских садов было идти до 18 минут"
    )

    assert not [w for w in result.warnings if "должны" in w or "этих" in w], result.warnings


LIVE_QUERY = (
    "хочу трёшка ближайшая к Финашке, с кухней от 20 квадратов, "
    "этажностью от 9 до 16 этажа, заселение с 2026 года по 2028 год, "
    "готовая отделка, рядом должны быть детские сады и школы, "
    "чтобы до этих детских садов и школ было идти до 18 минут"
)


def test_live_query_defects_closed():
    """Якорь на живой запрос пользователя: все фильтры доехали, остатка нет."""
    result = parse(LIVE_QUERY)
    criteria = result.criteria

    assert result.warnings == [], result.warnings
    assert criteria.rooms == [Rooms.THREE_PLUS]
    assert criteria.area_kitchen_min == 20
    assert criteria.floor_min == 9
    assert criteria.floor_max == 16
    assert criteria.finish == [Finish.READY]
    assert criteria.settlement_year_from == 2026
    assert criteria.settlement_year_to == 2028

    assert len(criteria.landmark_requirements) == 1
    landmark = criteria.landmark_requirements[0]
    assert landmark.name == "Финансовый университет"
    assert landmark.nearest_only is True

    by_category = {p.category: p for p in criteria.poi_requirements}
    assert set(by_category) == {"school", "kindergarten"}
    assert [p.max_distance_m for p in by_category.values()] == [1440, 1440]
