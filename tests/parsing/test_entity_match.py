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


def test_entity_match_negative_short_word_not_metro():
    # "мы" не должно ложно матчиться на метро/район "Мытищи" (CLAUDE.md,
    # раздел «Стресс-тестирование парсера»).
    text = "мы хотим квартиру для семьи"
    matches, _warnings = match_entities(text)
    names = {m.entity.name for m in matches}
    assert "Мытищи" not in names
    assert not any("Мытищи" in name for name in names)


def test_entity_match_negative_molodaya_not_molodezhnaya():
    # "молодая" не должно ложно матчиться на метро "Молодежная" (CLAUDE.md).
    text = "молодая семья ищет квартиру"
    matches, _warnings = match_entities(text)
    names = {m.entity.name for m in matches}
    assert "Молодежная" not in names


class TestOptionEntityMatching:
    """Регресс на ложное срабатывание matcher'а для options/option_groups.

    Баг: «квартира с отдельным санузлом и большими окнами» матчил
    «отдельным санузлом» на «Два и более санузла» (manybathrooms) —
    семантически неверно («отдельный» != «два и более»). Причина: окно из
    одного слова «санузлом» ловилось подстроковым триггером kw_partial
    («сануз» — часть слова «санузел»), из-за чего WRatio (с учётом
    partial_ratio) давал завышенную оценку (~78.75) против куда более
    длинного алиаса «несколько санузлов», а строгая перепроверка QRatio
    (~53.8, что ниже порога) пропускалась для «триггернутых» окон.
    """

    def test_bigwindows_true_positive_preserved(self):
        text = "квартира с большими окнами"
        matches, warnings = match_entities(text)
        slugs = {m.entity.slug for m in matches}
        assert "bigwindows" in slugs
        assert not warnings

    def test_bigwindows_panoramic_alias_preserved(self):
        text = "квартира с панорамными окнами"
        matches, _warnings = match_entities(text)
        slugs = {m.entity.slug for m in matches}
        assert "bigwindows" in slugs

    def test_otdelnyj_sanuzel_does_not_match_manybathrooms(self):
        # Главный регресс-кейс бага: одиночное «отдельным санузлом» не должно
        # давать manybathrooms. Фраза не теряется молча — она либо остаётся
        # непокрытой матчером (уйдёт в option_candidates/warnings в фасаде
        # parser.py, не в этом модуле), либо не попадает в criteria вовсе.
        text = "квартира с отдельным санузлом и большими окнами"
        matches, _warnings = match_entities(text)
        slugs = {m.entity.slug for m in matches}
        assert "manybathrooms" not in slugs
        assert "bigwindows" in slugs

    def test_otdelnyj_sanuzel_alone_matches_nothing(self):
        text = "квартира с отдельным санузлом"
        matches, _warnings = match_entities(text)
        assert matches == []

    def test_manybathrooms_true_positive_full_phrase(self):
        text = "квартира два санузла"
        matches, _warnings = match_entities(text)
        slugs = {m.entity.slug for m in matches}
        assert "manybathrooms" in slugs

    def test_manybathrooms_true_positive_two_and_more(self):
        text = "квартира с двумя и более санузлами"
        matches, _warnings = match_entities(text)
        slugs = {m.entity.slug for m in matches}
        assert "manybathrooms" in slugs

    def test_manybathrooms_true_positive_inflected(self):
        # "двумя санузлами" ~ алиас "2 санузла" — двусловное (не однословное)
        # окно, должно продолжать матчиться после фикса.
        text = "квартира с двумя санузлами"
        matches, _warnings = match_entities(text)
        slugs = {m.entity.slug for m in matches}
        assert "manybathrooms" in slugs

    def test_throughbathroom_true_positive_preserved(self):
        # «сквозной санузел» — другая опция (throughbathroom), тоже
        # двусловное триггернутое окно, должна продолжать матчиться.
        text = "квартира со сквозным санузлом"
        matches, _warnings = match_entities(text)
        slugs = {m.entity.slug for m in matches}
        assert "throughbathroom" in slugs

    def test_view_options_conjunction_preserved(self):
        text = "хочу с видом на воду и город"
        matches, _warnings = match_entities(text)
        names = {m.entity.name for m in matches}
        assert "Вид на воду" in names
        assert "Вид на город" in names


class TestShortEntityFalsePositives:
    """Регресс: короткий предлог/частица не должен матчиться на округ-аббревиатуру.

    Баг: «квартира со сквозным санузлом» помимо верного throughbathroom
    (option_groups) давал ЛОЖНЫЙ округ САО — предлог «со» и аббревиатура
    «САО» дают WRatio=QRatio=80.0 (случайное буквенное сходство коротких
    строк), что проходило порог строгой QRatio-проверки. Аналогично «во» ~
    «ВАО» = 80.0/80.0, «надо» ~ алиас «НАО» = 85.7/85.7. См. CLAUDE.md,
    раздел «Стресс-тестирование парсера», и комментарий у
    SHORT_ENTITY_EXACT_MAX_LEN в app/parsing/entity_match.py.
    """

    def test_so_preposition_does_not_match_sao_county(self):
        text = "квартира со сквозным санузлом"
        matches, _warnings = match_entities(text)
        names = {m.entity.name for m in matches}
        assert "САО" not in names
        slugs = {m.entity.slug for m in matches}
        assert "throughbathroom" in slugs

    def test_zao_county_true_positive_preserved(self):
        text = "трёшка в ЗАО, готовый дом"
        matches, _warnings = match_entities(text)
        names = {m.entity.name for m in matches}
        assert "ЗАО" in names

    def test_sao_county_true_positive_preserved(self):
        text = "двушка в САО"
        matches, _warnings = match_entities(text)
        names = {m.entity.name for m in matches}
        assert "САО" in names
