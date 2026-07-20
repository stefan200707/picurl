"""Тесты regex-правил извлечения структурных фактов (`app/parsing/rules.py`).

Табличные (`pytest.mark.parametrize`) тесты: вход → ожидаемые поля, плюс
проверки контракта ``(значение, consumed_spans)`` — диапазоны валидны и
указывают на «понятые» куски исходного текста.
"""

from datetime import datetime

import pytest

from app.parsing.rules import (
    AreaFacts,
    FloorFacts,
    PriceFacts,
    TimeFacts,
    apply_rules,
    extract_area,
    extract_finish,
    extract_floor,
    extract_housing_type,
    extract_only_available,
    extract_price,
    extract_ready,
    extract_rooms,
    extract_settlement_year,
    extract_sort,
    extract_time_to_metro,
    extract_unsupported,
)
from app.parsing.schema import HousingType, Rooms, Sort

# ---------------------------------------------------------------------------
# Комнатность
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Словоформы
        ("студия", [Rooms.STUDIO]),
        ("хочу студию", [Rooms.STUDIO]),
        ("однушка", [Rooms.ONE]),
        ("однушку в москве", [Rooms.ONE]),
        ("однокомнатную квартиру", [Rooms.ONE]),
        ("двушка", [Rooms.TWO]),
        ("хочу двушку у метро", [Rooms.TWO]),
        ("двухкомнатную", [Rooms.TWO]),
        ("трёшка", [Rooms.THREE_PLUS]),
        ("трешку", [Rooms.THREE_PLUS]),
        ("трёхкомнатную", [Rooms.THREE_PLUS]),
        ("четырёхкомнатная", [Rooms.THREE_PLUS]),
        ("многокомнатная", [Rooms.THREE_PLUS]),
        # Числовые формы
        ("1к", [Rooms.ONE]),
        ("2к", [Rooms.TWO]),
        ("3к", [Rooms.THREE_PLUS]),
        ("4к", [Rooms.THREE_PLUS]),
        ("1-комнатную", [Rooms.ONE]),
        ("2-комнатную", [Rooms.TWO]),
        ("3-комнатную", [Rooms.THREE_PLUS]),
        ("2-х комнатная", [Rooms.TWO]),
        ("3х комнатная", [Rooms.THREE_PLUS]),
        ("2 комнаты", [Rooms.TWO]),
        # Чип «3+»
        ("3+", [Rooms.THREE_PLUS]),
        ("3+ комнаты", [Rooms.THREE_PLUS]),
        ("2+", [Rooms.TWO, Rooms.THREE_PLUS]),
        # Перечисления
        ("1-2 комнатные", [Rooms.ONE, Rooms.TWO]),
        ("1, 2 и 3 комнатные", [Rooms.ONE, Rooms.TWO, Rooms.THREE_PLUS]),
        ("1 или 2 комнатную", [Rooms.ONE, Rooms.TWO]),
        ("студию или однушку", [Rooms.STUDIO, Rooms.ONE]),
        # Регистр и ё/е
        ("СТУДИЯ", [Rooms.STUDIO]),
        ("ТРЁШКА", [Rooms.THREE_PLUS]),
        # Дубликаты схлопываются
        ("двушка или двухкомнатная", [Rooms.TWO]),
        # Ничего
        ("квартира у метро", []),
        ("", []),
    ],
)
def test_extract_rooms(text: str, expected: list[Rooms]) -> None:
    rooms, spans = extract_rooms(text)
    assert rooms == expected
    assert bool(spans) == bool(expected)


@pytest.mark.parametrize(
    ("text", "expected_spans_len"),
    [
        ("но точно не студию", 1),
        ("точно не двушку", 1),
        ("кроме однушки", 1),
        ("без студий", 1),
    ],
)
def test_extract_rooms_negation(text: str, expected_spans_len: int) -> None:
    rooms, spans = extract_rooms(text)
    assert rooms == []
    assert len(spans) == expected_spans_len


def test_extract_rooms_spans_point_to_source() -> None:
    text = "хочу двушку у метро"
    rooms, spans = extract_rooms(text)
    assert rooms == [Rooms.TWO]
    assert len(spans) == 1
    start, end = spans[0]
    assert text[start:end] == "двушку"


def test_extract_rooms_does_not_eat_price_suffix() -> None:
    """«800к» — это 800 тысяч, а не комнатность."""
    rooms, spans = extract_rooms("бюджет 800к")
    assert rooms == []
    assert spans == []


# ---------------------------------------------------------------------------
# Цена
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # «до X»
        ("до 15 млн", PriceFacts(price_max=15_000_000)),
        ("до 15 млн.", PriceFacts(price_max=15_000_000)),
        ("до 15 миллионов", PriceFacts(price_max=15_000_000)),
        ("не дороже 12 млн", PriceFacts(price_max=12_000_000)),
        ("максимум 9 млн", PriceFacts(price_max=9_000_000)),
        ("в пределах 10 млн", PriceFacts(price_max=10_000_000)),
        ("до 800 тыс", PriceFacts(price_max=800_000)),
        ("до 15 000 000 руб", PriceFacts(price_max=15_000_000)),
        ("до 15000000 руб", PriceFacts(price_max=15_000_000)),
        # «от X»
        ("от 10 млн", PriceFacts(price_min=10_000_000)),
        ("не дешевле 8 млн", PriceFacts(price_min=8_000_000)),
        # Диапазоны
        ("10-15 млн", PriceFacts(price_min=10_000_000, price_max=15_000_000)),
        ("от 10 до 15 млн", PriceFacts(price_min=10_000_000, price_max=15_000_000)),
        ("от 6 до 9 миллионов", PriceFacts(price_min=6_000_000, price_max=9_000_000)),
        # «за X»
        ("за 15 миллионов", PriceFacts(price_max=15_000_000)),
        ("за 15 млн", PriceFacts(price_max=15_000_000)),
        ("за 15 млн рублей", PriceFacts(price_max=15_000_000)),
        ("за 15 млн руб.", PriceFacts(price_max=15_000_000)),
        # Бюджет
        ("бюджет 15 млн", PriceFacts(price_max=15_000_000)),
        ("бюджет 15м", PriceFacts(price_max=15_000_000)),
        ("бюджет 15", PriceFacts(price_max=15_000_000)),
        ("бюджет от 10 млн", PriceFacts(price_min=10_000_000)),
        ("цена до 12 млн", PriceFacts(price_max=12_000_000)),
        ("стоимость до 9 млн", PriceFacts(price_max=9_000_000)),
        # Слитные суффиксы м/к
        ("до 15м", PriceFacts(price_max=15_000_000)),
        ("за 800к", PriceFacts(price_max=800_000)),
        (
            "до 12 млн хотя если будет с отделкой под ключ, "
            "то готов рассмотреть и за 15 млн рублей",
            PriceFacts(price_max=15_000_000),
        ),
        # Дробные
        ("до 9,5 млн", PriceFacts(price_max=9_500_000)),
        ("до 9.5 млн", PriceFacts(price_max=9_500_000)),
        # Голые большие числа — рубли
        ("до 15000000", PriceFacts(price_max=15_000_000)),
        ("от 8000000", PriceFacts(price_min=8_000_000)),
        # Не цена: маленькие голые числа, этажи, площадь
        ("до 20 этажа", PriceFacts()),
        ("от 70 м²", PriceFacts()),
        ("70-100 метров", PriceFacts()),
        ("с 5 по 20 этаж", PriceFacts()),
        ("2к", PriceFacts()),  # комнатность, не «2 тысячи»
        ("", PriceFacts()),
    ],
)
def test_extract_price(text: str, expected: PriceFacts) -> None:
    price, spans = extract_price(text)
    assert price == expected
    has_value = expected.price_min is not None or expected.price_max is not None
    assert bool(spans) == has_value


def test_extract_price_spans_point_to_source() -> None:
    text = "хочу двушку до 15 млн у метро"
    price, spans = extract_price(text)
    assert price == PriceFacts(price_max=15_000_000)
    start, end = spans[0]
    assert text[start:end] == "до 15 млн"


# ---------------------------------------------------------------------------
# Площадь
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Общая площадь
        ("от 70 м²", AreaFacts(area_min=70)),
        ("от 70 м2", AreaFacts(area_min=70)),
        ("от 70 кв.м", AreaFacts(area_min=70)),
        ("от 70 квадратов", AreaFacts(area_min=70)),
        ("до 100 метров", AreaFacts(area_max=100)),
        ("70-100 метров", AreaFacts(area_min=70, area_max=100)),
        ("от 40 до 60 м²", AreaFacts(area_min=40, area_max=60)),
        ("площадь от 40", AreaFacts(area_min=40)),
        ("общей площадью 50-70", AreaFacts(area_min=50, area_max=70)),
        ("площадь до 90", AreaFacts(area_max=90)),
        ("50-70 кв. м", AreaFacts(area_min=50, area_max=70)),
        ("от 42,5 м²", AreaFacts(area_min=42.5)),
        # Кухня
        ("кухня от 8", AreaFacts(area_kitchen_min=8)),
        ("кухня от 8 м²", AreaFacts(area_kitchen_min=8)),
        ("с кухней от 10 метров", AreaFacts(area_kitchen_min=10)),
        ("кухня 8-12", AreaFacts(area_kitchen_min=8, area_kitchen_max=12)),
        ("кухня до 15", AreaFacts(area_kitchen_max=15)),
        ("кухня 10 м²", AreaFacts(area_kitchen_min=10)),
        # Кухня + общая вместе
        (
            "от 70 м², кухня от 8",
            AreaFacts(area_min=70, area_kitchen_min=8),
        ),
        # Не площадь
        ("до 15 млн", AreaFacts()),
        ("с 5 по 20 этаж", AreaFacts()),
        ("", AreaFacts()),
    ],
)
def test_extract_area(text: str, expected: AreaFacts) -> None:
    area, spans = extract_area(text)
    assert area == expected
    has_value = any(value is not None for value in expected)
    assert bool(spans) == has_value


def test_extract_area_kitchen_not_confused_with_total() -> None:
    """«кухня от 8» не должна стать общей площадью, и наоборот."""
    area, _ = extract_area("площадь от 40, кухня от 8")
    assert area == AreaFacts(area_min=40, area_kitchen_min=8)


# ---------------------------------------------------------------------------
# Время до метро
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("до 15 мин до метро", TimeFacts(time_on_foot=15)),
        ("до метро 10 минут", TimeFacts(time_on_foot=10)),
        ("в 15 минутах от метро", TimeFacts(time_on_foot=15)),
        ("в 5 мин от метро", TimeFacts(time_on_foot=5)),
        ("менее 20 минут пешком от метро", TimeFacts(time_on_foot=20)),
        ("до 15 мин на транспорте", TimeFacts(time_on_transport=15)),
        ("до метро до 15 мин на транспорте", TimeFacts(time_on_transport=15)),
        ("за 10 минут транспортом до метро", TimeFacts(time_on_transport=10)),
        ("у метро", TimeFacts()),
        ("", TimeFacts()),
    ],
)
def test_extract_time_to_metro(text: str, expected: TimeFacts) -> None:
    time, spans = extract_time_to_metro(text)
    assert time == expected
    has_value = any(value is not None for value in expected)
    assert bool(spans) == has_value


# ---------------------------------------------------------------------------
# Этаж
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("с 5 по 20 этаж", FloorFacts(floor_min=5, floor_max=20)),
        ("с 5 до 20 этажа", FloorFacts(floor_min=5, floor_max=20)),
        ("этаж с 3 по 7", FloorFacts(floor_min=3, floor_max=7)),
        ("этажность от 9 до 16", FloorFacts(floor_min=9, floor_max=16)),
        ("от 9 до 16 этажа", FloorFacts(floor_min=9, floor_max=16)),
        ("5-20 этаж", FloorFacts(floor_min=5, floor_max=20)),
        ("не ниже 4 этажа", FloorFacts(floor_min=4)),
        ("с 6-го этажа", FloorFacts(floor_min=6)),
        ("выше 7 этажа", FloorFacts(floor_min=7)),
        ("не выше 10 этажа", FloorFacts(floor_max=10)),
        ("до 9 этажа", FloorFacts(floor_max=9)),
        ("не первый", FloorFacts(not_first_floor=True)),
        ("не первый этаж", FloorFacts(not_first_floor=True)),
        ("не на первом этаже", FloorFacts(not_first_floor=True)),
        ("кроме первого", FloorFacts(not_first_floor=True)),
        ("выше первого этажа", FloorFacts(not_first_floor=True)),
        ("высокий этаж", FloorFacts(not_first_floor=True)),
        ("последний этаж", FloorFacts(last_floor=True)),
        ("на последнем этаже", FloorFacts(last_floor=True)),
        ("не последний этаж", FloorFacts(not_last_floor=True)),
        ("не на последнем этаже", FloorFacts(not_last_floor=True)),
        ("кроме последнего", FloorFacts(not_last_floor=True)),
        ("этаж не первый и не последний", FloorFacts(not_first_floor=True, not_last_floor=True)),
        # Комбинация
        (
            "не первый и не выше 12 этажа",
            FloorFacts(floor_max=12, not_first_floor=True),
        ),
        ("", FloorFacts()),
    ],
)
def test_extract_floor(text: str, expected: FloorFacts) -> None:
    floor, spans = extract_floor(text)
    assert floor == expected
    has_value = expected != FloorFacts()
    assert bool(spans) == has_value


# ---------------------------------------------------------------------------
# Отделка
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("с отделкой", [1]),
        ("квартира С ОТДЕЛКОЙ", [1]),
        ("с ремонтом", [1]),
        ("чистовая отделка", [1]),
        ("под ключ", [1]),
        ("без отделки", [0]),
        ("без ремонта", [0]),
        ("черновая", [0]),
        ("черновая отделка", [0]),
        ("какая отделка есть в вариантах", []),
        ("ремонт делать сами не хотим", []),
        ("у метро", []),
        ("", []),
    ],
)
def test_extract_finish(text: str, expected: list) -> None:
    finish, spans = extract_finish(text)
    assert finish == expected
    assert bool(spans) == bool(expected)


def test_extract_finish_conflict_last_mention_wins() -> None:
    finish, spans = extract_finish("без отделки, ну или с отделкой")
    assert finish == [0, 1]
    assert len(spans) == 2  # оба упоминания «съедены»


def test_extract_finish_conflict_last_mention_wins_2() -> None:
    finish, spans = extract_finish("черновая отделка, хотя нет, лучше с отделкой под ключ")
    assert finish == [0, 1]
    assert len(spans) == 3  # "черновая отделка" (False), "с отделкой" (True), "под ключ" (True)


# ---------------------------------------------------------------------------
# Заселение
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("заселение сразу", True),
        ("готовый дом", True),
        ("дом уже готов", True),
        ("дом сдан", True),
        ("сданный корпус", True),
        ("можно сразу заехать", True),
        ("ключи сразу", True),
        ("строится", None),
        ("", None),
    ],
)
def test_extract_ready(text: str, expected: bool | None) -> None:
    ready, spans = extract_ready(text)
    assert ready is expected
    assert bool(spans) == bool(expected)


@pytest.mark.parametrize(
    ("text", "expected_min", "expected_max"),
    [
        ("заселение в 2030 году", 2030, 2030),
        ("сдача 2026", 2026, 2026),
        ("заселение с 2026 по 2028", 2026, 2028),
        ("въезд в этом году", datetime.now().year, datetime.now().year),
        ("заселение до 2030 года", None, 2030),
        ("заселение не позднее 2027", None, 2027),
        ("сдача от 2025 года", 2025, None),
        ("заселение после 2026", 2026, None),
        ("что-то другое", None, None),
        ("", None, None),
    ],
)
def test_extract_settlement_year(
    text: str, expected_min: int | None, expected_max: int | None
) -> None:
    y_min, y_max, spans = extract_settlement_year(text)
    assert y_min == expected_min
    assert y_max == expected_max
    has_value = y_min is not None or y_max is not None
    assert bool(spans) == has_value


# ---------------------------------------------------------------------------
# Сортировка
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("подешевле", Sort.PRICE_ASC),
        ("сначала дешевле", Sort.PRICE_ASC),
        ("сначала подешевле", Sort.PRICE_ASC),
        ("по возрастанию цены", Sort.PRICE_ASC),
        ("от дешевых к дорогим", Sort.PRICE_ASC),
        ("подороже", Sort.PRICE_DESC),
        ("сначала дорогие", Sort.PRICE_DESC),
        ("по убыванию цены", Sort.PRICE_DESC),
        ("площадь побольше", Sort.AREA_DESC),
        ("побольше площадью", Sort.AREA_DESC),
        ("сначала просторные", Sort.AREA_DESC),
        ("по убыванию площади", Sort.AREA_DESC),
        ("площадь поменьше", Sort.AREA_ASC),
        ("по возрастанию площади", Sort.AREA_ASC),
        ("сначала маленькие", Sort.AREA_ASC),
        # Не сортировка
        ("двушка у метро", None),
        ("", None),
    ],
)
def test_extract_sort(text: str, expected: Sort | None) -> None:
    sort, spans = extract_sort(text)
    assert sort is expected
    assert bool(spans) == bool(expected)


def test_sort_field_order_mapping() -> None:
    """Ключи сортировки дают правильные sortBy/orderBy (контракт промпта 02)."""
    sort, _ = extract_sort("подешевле")
    assert sort is not None
    assert (sort.field, sort.order) == ("price", "asc")


# ---------------------------------------------------------------------------
# Тип жилья, доступность, неподдерживаемое
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("только квартиры", HousingType.FLATS_ONLY),
        ("без апартаментов", HousingType.FLATS_ONLY),
        ("не апартаменты", HousingType.FLATS_ONLY),
        ("апартаменты тоже можно", None),
        ("", None),
    ],
)
def test_extract_housing_type(text: str, expected: HousingType | None) -> None:
    housing, spans = extract_housing_type(text)
    assert housing is expected
    assert bool(spans) == bool(expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("не бронь", True),
        ("без брони", True),
        ("не забронированные", True),
        ("только свободные", True),
        ("только доступные", True),
        ("квартира по доступной цене", False),
        ("двушка у метро", False),
        ("", False),
    ],
)
def test_extract_only_available(text: str, expected: bool) -> None:
    available, spans = extract_only_available(text)
    assert available is expected
    assert bool(spans) == expected


@pytest.mark.parametrize(
    ("text", "fragments"),
    [
        ("вторичка", [("вторичка", "фильтр не поддерживается сайтом ПИК")]),
        ("вторичный рынок", [("вторичный рынок", "фильтр не поддерживается сайтом ПИК")]),
        ("на вторичном рынке", [("вторичном рынке", "фильтр не поддерживается сайтом ПИК")]),
        ("вторичное жильё", [("вторичное жильё", "фильтр не поддерживается сайтом ПИК")]),
        ("новостройка", []),
        ("", []),
    ],
)
def test_extract_unsupported(text: str, fragments: list[tuple[str, str]]) -> None:
    found, spans = extract_unsupported(text)
    assert found == fragments
    assert len(spans) == len(fragments)


def test_extract_unsupported_returns_original_case() -> None:
    """Фрагмент для warning берётся из исходного текста (с регистром)."""
    found, _ = extract_unsupported("рассмотрю Вторичку")
    assert found == [("Вторичку", "фильтр не поддерживается сайтом ПИК")]


# ---------------------------------------------------------------------------
# Агрегат apply_rules
# ---------------------------------------------------------------------------


def test_apply_rules_full_sentence() -> None:
    text = "хочу двушку у метро, до 15 млн, с отделкой, подешевле"
    outcome = apply_rules(text)
    criteria = outcome.criteria
    assert criteria.rooms == [Rooms.TWO]
    assert criteria.price_max == 15_000_000
    assert criteria.finish == [1]
    assert criteria.sort is Sort.PRICE_ASC
    assert outcome.unsupported == []
    # «съеденные» диапазоны валидны, отсортированы и указывают на понятые куски
    consumed_text = {text[start:end] for start, end in outcome.consumed}
    assert {"двушку", "до 15 млн", "с отделкой", "подешевле"} <= consumed_text
    assert outcome.consumed == sorted(outcome.consumed)
    assert all(0 <= start < end <= len(text) for start, end in outcome.consumed)


def test_apply_rules_rich_query() -> None:
    text = (
        "1-2 комнатные от 10 до 15 млн, площадь от 40, кухня от 8, "
        "с 5 по 20 этаж, не первый, без отделки, только квартиры, "
        "не бронь, дом сдан"
    )
    criteria = apply_rules(text).criteria
    assert criteria.rooms == [Rooms.ONE, Rooms.TWO]
    assert criteria.price_min == 10_000_000
    assert criteria.price_max == 15_000_000
    assert criteria.area_min == 40
    assert criteria.area_kitchen_min == 8
    assert criteria.floor_min == 5
    assert criteria.floor_max == 20
    assert criteria.not_first_floor is True
    assert criteria.finish == [0]
    assert criteria.housing_type is HousingType.FLATS_ONLY
    assert criteria.only_available is True
    assert criteria.ready is True


def test_apply_rules_unsupported_secondary_market() -> None:
    outcome = apply_rules("двушка на вторичном рынке до 10 млн")
    assert outcome.criteria.rooms == [Rooms.TWO]
    assert outcome.criteria.price_max == 10_000_000
    assert outcome.unsupported == [("вторичном рынке", "фильтр не поддерживается сайтом ПИК")]


def test_apply_rules_empty_and_unrecognized_text() -> None:
    assert apply_rules("").criteria.to_public_dict() == {}
    outcome = apply_rules("что-нибудь красивое с видом")
    assert outcome.criteria.to_public_dict() == {}
    assert outcome.consumed == []


def test_apply_rules_is_pure() -> None:
    """Повторный вызов на том же тексте даёт идентичный результат."""
    text = "трёшка до 20 млн, последний этаж, подороже"
    assert apply_rules(text) == apply_rules(text)


def test_apply_rules_price_area_floor_do_not_collide() -> None:
    """Числа этажей и метров не утекают в цену и наоборот."""
    criteria = apply_rules("70-100 метров, с 5 по 20 этаж, до 15 млн").criteria
    assert criteria.area_min == 70
    assert criteria.area_max == 100
    assert criteria.floor_min == 5
    assert criteria.floor_max == 20
    assert criteria.price_min is None
    assert criteria.price_max == 15_000_000


def test_extract_required_tags():
    from app.parsing.rules import extract_required_tags

    tags, spans = extract_required_tags("хочу готовые квартиры и специальная цена до 15.07")
    assert tags == ["zos", "crossed"]
    assert len(spans) == 2
