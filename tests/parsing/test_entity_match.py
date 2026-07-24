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

    def test_vid_vo_dvor_does_not_also_match_vid_na_vodu(self):
        # Регресс: «панорамными окнами и видом во двор» давал ДВЕ опции вида —
        # верную «Вид во двор» (vidVoDvor) и ложную «Вид на воду» (vidNaVodu).
        # Причина: окно «панорамными окнами и видом» матчилось на алиас
        # «с видом на воду» с WRatio≈85.5 (partial_ratio: «видом»→«с видом»),
        # хотя token_set_ratio всего 50 — общих токенов почти нет.
        text = "квартира с панорамными окнами и видом во двор"
        matches, _warnings = match_entities(text)
        slugs = {m.entity.slug for m in matches}
        assert "vidVoDvor" in slugs
        assert "bigwindows" in slugs
        assert "vidNaVodu" not in slugs

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


class TestUntriggeredToponymFalsePositives:
    """Регресс: обиходное слово БЕЗ анкера не должно матчиться на станцию/район.

    Класс ложняков из живых прогонов: однословное окно без триггера
    («метро»/«район»/«у…») морфологически близко к названию топонима, но это
    НЕ упоминание топонима, а прилагательное/существительное соседней фразы:
    «хорошей» школой → Хорошево, «первое/первом» → Перово, «спортивным»
    комплексом → Спортивная, «для внуков» → Внуково. Строковая близость тут
    НЕОТЛИЧИМА от законных склонений (QRatio 80-92 у обоих, а у «химках»→Химки
    вообще 73 — ниже ложных), поэтому порогом класс не режется: разделяет только
    лексика. Закрыто точечными стоп-словами (как «молодая»/«хорошая»), см.
    app/parsing/stopwords.py. Истинные упоминания с анкером/точные — не задеты.
    """

    def test_horoshej_not_horoshevo(self):
        matches, _w = match_entities("квартира рядом с хорошей школой")
        assert "Хорошево" not in {m.entity.name for m in matches}

    def test_pervoe_zhile_not_perovo(self):
        matches, _w = match_entities("ищу первое жильё")
        assert "Перово" not in {m.entity.name for m in matches}

    def test_pervom_etazhe_not_perovo(self):
        matches, _w = match_entities("квартира на первом или втором этаже")
        assert "Перово" not in {m.entity.name for m in matches}

    def test_sportivnym_kompleksom_not_sportivnaya(self):
        matches, _w = match_entities("рядом со спортивным комплексом")
        assert "Спортивная" not in {m.entity.name for m in matches}

    def test_vnukov_not_vnukovo(self):
        matches, _w = match_entities("садик для внуков")
        names = {m.entity.name for m in matches}
        assert "Внуково" not in names
        assert "Аэропорт Внуково" not in names

    def test_triggered_toponym_still_matches(self):
        # С явным анкером «метро …»/«район …» близкое склонение обязано ловиться.
        assert "Перово" in {m.entity.name for m in match_entities("район Перово")[0]}
        assert "Хорошево" in {m.entity.name for m in match_entities("у метро Хорошёво")[0]}

    def test_exact_bare_toponym_still_matches(self):
        # Точное имя без триггера (QRatio=100) — проходит.
        assert "Перово" in {m.entity.name for m in match_entities("Перово")[0]}


class TestProximityMarkerTriggerCoverage:
    """Единый источник маркеров близости (Milestone AI-20, Фикс 1).

    Раньше TRIGGERS в entity_match знал только «у метро»/«на метро»/«рядом с
    метро»/«м.»/«м» — в отличие от rules/landmark.py, где список форм маркера
    был полным. «недалеко от метро», «около метро» и т.п. триггер метро не
    давали вовсе.
    """

    @pytest.mark.parametrize(
        "prefix",
        [
            "квартира у метро ",
            "квартира на метро ",
            "квартира рядом с метро ",
            "квартира недалеко от метро ",
            "квартира неподалеку от метро ",
            "квартира неподалёку от метро ",
            "квартира около метро ",
            "квартира возле метро ",
            "квартира вблизи метро ",
        ],
    )
    def test_get_trigger_type_recognizes_all_proximity_markers(self, prefix: str):
        from app.parsing.entity_match import get_trigger_type

        ttype, _start = get_trigger_type(prefix)
        assert ttype == "metro"


class TestProximityMarkerSpanAbsorption:
    """Спан сущности поглощает прилегающий маркер близости независимо от типа
    найденной сущности (Milestone AI-20, Фикс 3).

    Раньше поглощение маркера в consumed span было завязано на
    ``get_trigger_type`` (типоспецифичные TRIGGERS) — фразы вроде «недалеко от
    метро X» матчили саму сущность X, но маркер «недалеко от» оставался
    непонятым текстом и уходил в warnings.
    """

    def test_metro_marker_absorbed_into_entity_span(self):
        text = "квартира недалеко от метро Сокол"
        matches, _warnings = match_entities(text)
        assert len(matches) == 1
        start, end = matches[0].span
        assert text[start:end] == "недалеко от метро Сокол"

    def test_latin_homoglyph_metro_marker_absorbed_into_entity_span(self):
        """Гомоглиф латинской 'e' в «метро» не мешает поглощению маркера
        (Фикс 2 + Фикс 3 совместно)."""
        latin_e = "e"
        text = f"квартира у м{latin_e}тро Сокол"
        matches, _warnings = match_entities(text)
        assert len(matches) == 1
        assert matches[0].entity.name == "Сокол"
        start, end = matches[0].span
        assert text[start:end] == f"у м{latin_e}тро Сокол"


class TestSameNameDifferentTypeDisambiguation:
    """«Коммунарка» существует и как метро, и как район (Milestone AI-20,
    Фикс 5). Раньше ambiguity-warning был завязан на РАЗНОЕ ``entry.name`` —
    для одноимённых сущностей разных типов проверка не срабатывала, и обе
    молча добавлялись в результат разом.
    """

    def test_metro_trigger_resolves_to_metro_only(self):
        matches, warnings = match_entities("квартира у метро Коммунарка")
        assert len(matches) == 1
        assert matches[0].type == "metro"
        assert not warnings

    def test_district_trigger_resolves_to_district_only(self):
        matches, warnings = match_entities("квартира в районе Коммунарка")
        assert len(matches) == 1
        assert matches[0].type == "district"
        assert not warnings

    def test_no_trigger_gives_explicit_ambiguity_warning_not_both(self):
        matches, warnings = match_entities("квартира в Коммунарке")
        assert len(matches) == 1
        assert any("Коммунарка" in w and "Неоднозначность" in w for w in warnings)
