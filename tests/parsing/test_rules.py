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
from app.parsing.schema import Finish, HousingType, Rooms, Sort

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
        # Числительные прописью
        ("одна или две комнаты", [Rooms.ONE, Rooms.TWO]),
        ("две или три комнаты", [Rooms.TWO, Rooms.THREE_PLUS]),
        ("одну комнату", [Rooms.ONE]),
        ("три-четыре комнаты", [Rooms.THREE_PLUS]),
        ("пять комнат", [Rooms.THREE_PLUS]),
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
        ("кроме двух комнат", 1),
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


@pytest.mark.parametrize(
    "text",
    [
        "у мцд-2 комнатная квартира",
        "у мцд-3 комнатная квартира",
    ],
)
def test_extract_rooms_does_not_eat_line_number(text: str) -> None:
    """Регрессия (Milestone AI-16): цифра из «МЦД-N» перед словом «комнатная» не
    должна становиться комнатностью — тот же класс бага, что и в цене/площади.
    """
    rooms, spans = extract_rooms(text)
    assert rooms == []
    assert spans == []


def test_extract_rooms_no_line_number_leak_with_word_form() -> None:
    """«МЦД-3 трёшка» распознаёт «трёшку» по словоформе, а не по цифре «3»."""
    text = "мцд-3 трешка"
    rooms, spans = extract_rooms(text)
    assert rooms == [Rooms.THREE_PLUS]
    for start, end in spans:
        assert "мцд" not in text[start:end].lower()


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
        # Регрессия: номер линии «МЦД-N»/«МЦК-N» не должен становиться нижней
        # границей диапазона (баг, Milestone AI-16: «мцд-3 до 12 млн» давал
        # price_min=3_000_000 — цифра из «МЦД-3» ошибочно трактовалась как
        # низ диапазона «3 … до 12 млн»).
        ("у мцд-3 до 12 млн", PriceFacts(price_max=12_000_000)),
        ("у мцд-2 до 12 млн", PriceFacts(price_max=12_000_000)),
        ("однушка у мцд-4 до 15 млн", PriceFacts(price_max=15_000_000)),
        ("рядом с мцк-1 до 10 млн", PriceFacts(price_max=10_000_000)),
        # «за X»
        ("за 15 миллионов", PriceFacts(price_max=15_000_000)),
        ("за 15 млн", PriceFacts(price_max=15_000_000)),
        ("за 15 млн рублей", PriceFacts(price_max=15_000_000)),
        ("за 15 млн руб.", PriceFacts(price_max=15_000_000)),
        # «единица за X» — обратный разговорный порядок слов (баг №2)
        ("лямов за 15", PriceFacts(price_max=15_000_000)),
        ("миллионов за 15", PriceFacts(price_max=15_000_000)),
        ("млн за 15", PriceFacts(price_max=15_000_000)),
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


def test_extract_price_unit_before_za_bug_report_example() -> None:
    """Ровно кейс из описания бага: «...хату двушку лямов за 15 у метро»."""
    text = "кароче хочу хату двушку лямов за 15 у метро"
    price, spans = extract_price(text)
    assert price == PriceFacts(price_max=15_000_000)
    assert spans  # цена не потеряна целиком


@pytest.mark.parametrize(
    "text",
    [
        "однушка у мцд-3 до 12 млн",
        "однушка у мцд-2 до 12 млн",
    ],
)
def test_extract_price_does_not_consume_line_number(text: str) -> None:
    """Регрессия бага (Milestone AI-16): цифра из «МЦД-N» не должна попадать в
    span ценового правила — её обязан «съесть» ``station_class.py``, а не
    ценовой диапазон. ``price_min`` остаётся ``None``, а «мцд-N» целиком вне
    consumed-диапазона цены.
    """
    price, spans = extract_price(text)
    assert price == PriceFacts(price_max=12_000_000)
    for start, end in spans:
        assert "мцд" not in text[start:end].lower()


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
        # Регрессия: номер линии «МЦД-N» не должен становиться нижней
        # границей диапазона площади (тот же класс бага, что и в цене,
        # Milestone AI-16: «мцд-3 до 60 метров» давал area_min=3.0).
        ("квартира у мцд-3 до 60 метров", AreaFacts(area_max=60)),
        ("двушка у мцд-4 до 60 метров", AreaFacts(area_max=60)),
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


@pytest.mark.parametrize(
    "text",
    [
        "квартира у мцд-3 до 60 метров",
        "двушка у мцд-4 до 60 метров",
    ],
)
def test_extract_area_does_not_consume_line_number(text: str) -> None:
    """Та же защита, что и у цены (Milestone AI-16): «мцд-N» не попадает в
    consumed-диапазон площади — его съедает ``station_class.py``.
    """
    area, spans = extract_area(text)
    assert area == AreaFacts(area_max=60)
    for start, end in spans:
        assert "мцд" not in text[start:end].lower()


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
        # Предчистовая / whitebox (WHITE_BOX=2). Латиница проходит через
        # гомоглиф-нормализацию, но матчер её ловит; паразитного READY нет.
        ("предчистовая отделка", [Finish.WHITE_BOX]),
        ("whitebox", [Finish.WHITE_BOX]),
        ("white box", [Finish.WHITE_BOX]),
        ("с отделкой whitebox", [Finish.WHITE_BOX]),
        ("с whitebox отделкой", [Finish.WHITE_BOX]),
        ("вайтбокс", [Finish.WHITE_BOX]),
        ("вайт-бокс", [Finish.WHITE_BOX]),
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
    """Взаимоисключающие упоминания: побеждает последнее по позиции (CLAUDE.md)."""
    finish, spans = extract_finish("без отделки, ну или с отделкой")
    assert finish == [1]  # только READY — «без отделки» отброшено как проигравшее
    assert len(spans) == 2  # оба упоминания всё равно «съедены» (span'ы не теряются)


def test_extract_finish_conflict_last_mention_wins_2() -> None:
    """Пример из CLAUDE.md/бага: не должно получаться hasFinish=1,0 (оба значения)."""
    finish, spans = extract_finish("черновая отделка, хотя нет, лучше с отделкой под ключ")
    assert finish == [1]  # только READY — «черновая отделка» (NONE) отброшена
    assert len(spans) == 3  # "черновая отделка" (False), "с отделкой" (True), "под ключ" (True)


def test_extract_finish_conflict_bug_report_example() -> None:
    """Ровно кейс из описания бага: «с отделкой, хотя нет, лучше без отделки»."""
    finish, spans = extract_finish("с отделкой, хотя нет, лучше без отделки, до 8 млн")
    assert finish == [0]  # NONE — последнее по позиции упоминание побеждает
    assert len(spans) == 2


def test_extract_finish_multiple_positive_types_not_conflicting() -> None:
    """Разные ПОЛОЖИТЕЛЬНЫЕ разновидности отделки не противоречат друг другу."""
    finish, spans = extract_finish("готовая отделка и с мебелью")
    assert finish == [Finish.READY, Finish.FURNISHED]
    assert len(spans) == 2


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


_SECONDARY = "вторичное жильё не поддерживается pik.ru (только новостройки) — пропущено"
_SIDE_OF_LIGHT = "фильтр по стороне света не поддерживается pik.ru — пропущен"
_SPLIT_BATHROOM = "фильтр «раздельный санузел» не поддерживается pik.ru — пропущен"
_HOUSE_MATERIAL = "фильтр по материалу дома не поддерживается pik.ru — пропущен"


@pytest.mark.parametrize(
    ("text", "fragments"),
    [
        # --- Вторичка (только новостройки) ---
        ("вторичка", [("вторичка", _SECONDARY)]),
        ("вторичный рынок", [("вторичный рынок", _SECONDARY)]),
        ("на вторичном рынке", [("вторичном рынке", _SECONDARY)]),
        ("вторичное жильё", [("вторичное жильё", _SECONDARY)]),
        # --- Сторона света / вид «на светлую сторону» (эталон милстоуна) ---
        ("вид на светлую сторону", [("светлую сторону", _SIDE_OF_LIGHT)]),
        ("на солнечную сторону", [("солнечную сторону", _SIDE_OF_LIGHT)]),
        ("окна на южную сторону", [("южную сторону", _SIDE_OF_LIGHT)]),
        ("на северную сторону", [("северную сторону", _SIDE_OF_LIGHT)]),
        ("выходит на сторону света", [("сторону света", _SIDE_OF_LIGHT)]),
        # --- Раздельный санузел (в схеме нет) ---
        ("раздельный санузел", [("раздельный санузел", _SPLIT_BATHROOM)]),
        ("с раздельным санузлом", [("раздельным санузлом", _SPLIT_BATHROOM)]),
        # --- Материал дома (в схеме нет) ---
        ("кирпичный дом", [("кирпичный дом", _HOUSE_MATERIAL)]),
        ("монолитный дом", [("монолитный дом", _HOUSE_MATERIAL)]),
        ("панельный дом", [("панельный дом", _HOUSE_MATERIAL)]),
        # --- Поддерживается под другим именем → НЕ в каталоге ---
        ("панорамные окна", []),  # «Большие окна» (bigwindows)
        ("окна во двор", []),  # «Вид во двор» (vidVoDvor)
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
    assert found == [("Вторичку", _SECONDARY)]


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
    assert outcome.unsupported == [("вторичном рынке", _SECONDARY)]


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


def test_apply_rules_range_idiom_does_not_leak_into_floor() -> None:
    """«от X до Y» у площади/цены не создаёт паразитный диапазон этажей.

    Регресс: голый паттерн диапазона этажа (_FLOOR_RANGE_E, без слова «этаж»)
    повторно матчил число, уже съеденное площадью/ценой, и подставлял
    floorFrom/floorTo (для площади 35–45 это обнуляло выдачу).
    """
    area = apply_rules("однушка площадью от 35 до 45 метров").criteria
    assert area.area_min == 35
    assert area.area_max == 45
    assert area.floor_min is None
    assert area.floor_max is None

    price = apply_rules("двушка ценой от 15 до 22 миллионов").criteria
    assert price.price_min == 15_000_000
    assert price.price_max == 22_000_000
    assert price.floor_min is None
    assert price.floor_max is None


def test_apply_rules_bare_floor_range_still_works() -> None:
    """Голый «от X до Y» без площади/цены по-прежнему трактуется как этаж."""
    criteria = apply_rules("квартира от 5 до 10").criteria
    assert criteria.floor_min == 5
    assert criteria.floor_max == 10


def test_extract_required_tags():
    from app.parsing.rules import extract_required_tags

    tags, spans = extract_required_tags("хочу готовые квартиры и специальная цена до 15.07")
    assert tags == ["zos", "crossed"]
    assert len(spans) == 2

    tags, spans = extract_required_tags("выгода до -20%")
    assert tags == ["outlet"]

    tags, spans = extract_required_tags("спецпредложение до 01.09")
    assert tags == ["crossed"]


def test_extract_poi_requirements():
    from app.geo.poi import POICategory
    from app.parsing.rules.poi import extract_poi_requirements

    poi_reqs, center_req, _spans = extract_poi_requirements(
        "ищу квартиру в центре рядом со школой и садиком, нужен лес"
    )
    assert center_req is True
    assert len(poi_reqs) == 3
    assert poi_reqs[0].category == POICategory.SCHOOL
    assert poi_reqs[1].category == POICategory.KINDERGARTEN
    assert poi_reqs[2].category == POICategory.PARK_FOREST

    poi_reqs, center_req, _spans = extract_poi_requirements("где-то около парковки и магазина")
    assert center_req is False
    assert len(poi_reqs) == 2
    assert poi_reqs[0].category == POICategory.SHOP
    assert poi_reqs[1].category == POICategory.PARKING


def test_poi_school_does_not_match_shkolnik():
    """«школьник» — человек, не объект: открытый стем «школ\\w+» не должен ловить его."""
    from app.geo.poi import POICategory
    from app.parsing.rules.poi import extract_poi_requirements

    poi_reqs, _center, _spans = extract_poi_requirements("живу со школьником рядом со школой")
    # только «школа», не «школьник»
    assert [req.category for req in poi_reqs] == [POICategory.SCHOOL]


@pytest.mark.parametrize(
    "text",
    [
        "рядом с поликлиникой",
        "около больницы",
        "рядом с аптекой",
        "рядом с роддомом",
        "недалеко медцентр",
        "рядом с клиникой",
    ],
)
def test_extract_poi_medical(text: str):
    """Медицинские POI (поликлиника/больница/аптека/роддом/медцентр/клиника)."""
    from app.geo.poi import POICategory
    from app.parsing.rules.poi import extract_poi_requirements

    poi_reqs, _center, _spans = extract_poi_requirements(text)
    assert len(poi_reqs) == 1
    assert poi_reqs[0].category == POICategory.MEDICAL


def test_poi_only_new_flag():
    """«нов-» перед POI → only_new=True (per-instance), иначе False."""
    from app.geo.poi import POICategory
    from app.parsing.rules.poi import extract_poi_requirements

    poi_reqs, _center, _spans = extract_poi_requirements("новые детские сады рядом")
    assert len(poi_reqs) == 1
    assert poi_reqs[0].category == POICategory.KINDERGARTEN
    assert poi_reqs[0].only_new is True

    poi_reqs, _center, _spans = extract_poi_requirements("детские сады рядом")
    assert len(poi_reqs) == 1
    assert poi_reqs[0].only_new is False


def test_poi_only_new_is_per_instance():
    """Один текст: «новые сады» → only_new, «школы» → нет (флаг per-instance)."""
    from app.geo.poi import POICategory
    from app.parsing.rules.poi import extract_poi_requirements

    poi_reqs, _center, _spans = extract_poi_requirements("новые детские сады и школы рядом")
    by_cat = {req.category: req for req in poi_reqs}
    assert by_cat[POICategory.KINDERGARTEN].only_new is True
    assert by_cat[POICategory.SCHOOL].only_new is False


def test_poi_max_distance_parsed():
    """Дистанция рядом с POI пишется в max_distance_m (закрытие AUDIT 2.11)."""
    from app.parsing.rules.poi import extract_poi_requirements

    poi_reqs, _center, _spans = extract_poi_requirements("школа в 300 метрах")
    assert poi_reqs[0].max_distance_m == 300

    poi_reqs, _center, _spans = extract_poi_requirements("детский сад не дальше 500 м")
    assert poi_reqs[0].max_distance_m == 500

    poi_reqs, _center, _spans = extract_poi_requirements("школа рядом")
    assert poi_reqs[0].max_distance_m is None


def test_poi_bare_sad_bug_report_example():
    """Ровно кейс из описания бага: голое разговорное «сады» не теряется."""
    from app.geo.poi import POICategory
    from app.parsing.rules.poi import extract_poi_requirements

    poi_reqs, _center, _spans = extract_poi_requirements(
        "двушка, новые сады в 300 метрах, школа не дальше 500 м, ТиНАО"
    )
    by_cat = {req.category: req for req in poi_reqs}
    kindergarten = by_cat[POICategory.KINDERGARTEN]
    assert kindergarten.only_new is True
    assert kindergarten.max_distance_m == 300
    assert by_cat[POICategory.SCHOOL].max_distance_m == 500


@pytest.mark.parametrize(
    "text",
    [
        "сады",
        "садов поблизости",
        "новые сады",
    ],
)
def test_poi_bare_sad_plural_forms(text: str):
    """Множественное число «сад» (сады/садов/...) — детсад, маркер не нужен."""
    from app.geo.poi import POICategory
    from app.parsing.rules.poi import extract_poi_requirements

    poi_reqs, _center, _spans = extract_poi_requirements(text)
    assert len(poi_reqs) == 1
    assert poi_reqs[0].category == POICategory.KINDERGARTEN


def test_poi_bare_sad_singular_requires_marker():
    """Единственное число «сад» без маркера «нов-» НЕ матчится (риск ложных срабатываний)."""
    from app.geo.poi import POICategory
    from app.parsing.rules.poi import extract_poi_requirements

    # Без маркера — бытовое «сад» лучше пропустить, чем ложно сработать.
    poi_reqs, _center, _spans = extract_poi_requirements("сад рядом")
    assert poi_reqs == []

    # С обязательным маркером «нов-» — распознаётся.
    poi_reqs, _center, _spans = extract_poi_requirements("новый сад рядом")
    assert len(poi_reqs) == 1
    assert poi_reqs[0].category == POICategory.KINDERGARTEN
    assert poi_reqs[0].only_new is True


@pytest.mark.parametrize(
    "text",
    [
        "хочу квартиру рядом с Ботаническим садом",
        "хочу квартиру у метро Александровский сад",
        "живу у метро Ботанический сад",
    ],
)
def test_poi_bare_sad_does_not_match_metro_toponyms(text: str):
    """Регрессия: топонимы-станции метро («Ботанический сад» и т.п.) не дают
    ложного POI-требования по детсаду — единственное число без маркера «нов-».
    """
    from app.geo.poi import POICategory
    from app.parsing.rules.poi import extract_poi_requirements

    poi_reqs, _center, _spans = extract_poi_requirements(text)
    assert all(req.category != POICategory.KINDERGARTEN for req in poi_reqs)


def test_extract_landmark_near_mgu():
    """«рядом с МГУ» → LandmarkRequirement с координатами ориентира."""
    from app.parsing.rules.landmark import extract_landmark_requirements

    reqs, spans = extract_landmark_requirements("трёшку самую ближайшую к МГУ")
    assert len(reqs) == 1
    assert reqs[0].name == "МГУ им. Ломоносова"
    assert reqs[0].category == "university"
    assert reqs[0].lat is not None and reqs[0].lon is not None
    assert spans  # маркер+имя засчитаны «понятыми»


def test_extract_landmark_markers_and_declension():
    """Разные маркеры близости и склонения имени распознаются."""
    from app.parsing.rules.landmark import extract_landmark_requirements

    reqs, _ = extract_landmark_requirements("квартира у Кремля")
    assert [r.name for r in reqs] == ["Московский Кремль"]

    reqs, _ = extract_landmark_requirements("хочу жильё поближе к Сколково")
    assert [r.name for r in reqs] == ["Инновационный центр Сколково"]

    reqs, _ = extract_landmark_requirements("недалеко от ВДНХ")
    assert [r.name for r in reqs] == ["ВДНХ"]


def test_extract_landmark_no_false_positive():
    """Маркер-предлог без ориентира не порождает ложное требование."""
    from app.parsing.rules.landmark import extract_landmark_requirements

    reqs, _ = extract_landmark_requirements("хочу двушку у метро до 15 млн")
    assert reqs == []


def test_landmark_flows_into_criteria():
    """apply_rules прокидывает ориентир в Criteria.landmark_requirements."""
    from app.parsing.rules import apply_rules

    outcome = apply_rules("двушка рядом с МГУ")
    assert len(outcome.criteria.landmark_requirements) == 1
    assert outcome.criteria.landmark_requirements[0].name == "МГУ им. Ломоносова"


# --- Класс станций «любая станция линии» (Milestone AI-15) ------------------
#
# Обобщение ориентиров (промпт 23) на КЛАСС точек: «рядом с МЦД не важно какой
# станции» — пользователю подходит ЛЮБАЯ станция класса, а не одна конкретная
# точка. Регрессия бага: запрос «Нужна двушка рядом с МЦД не важно какой
# станции и округа, до 15 млн» терял этот фрагмент в warnings целиком.


def test_extract_station_class_explicit_line():
    """«рядом с МЦД» / «у МЦД» / «около МЦК» → line_prefix класса линии."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, spans = extract_station_class_requirements("двушка рядом с МЦД до 15 млн")
    assert len(reqs) == 1
    assert reqs[0].line_prefix == "МЦД"
    assert spans

    reqs, _ = extract_station_class_requirements("хочу квартиру у МЦД")
    assert [r.line_prefix for r in reqs] == ["МЦД"]

    reqs, _ = extract_station_class_requirements("квартира около МЦК")
    assert [r.line_prefix for r in reqs] == ["МЦК"]


def test_extract_station_class_specific_numbered_line():
    """Конкретная нумерованная линия («МЦД-2») распознаётся отдельно от «МЦД»."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, _ = extract_station_class_requirements("рядом с МЦД-2")
    assert [r.line_prefix for r in reqs] == ["МЦД-2"]


def test_extract_station_class_qualifier_tail_consumed():
    """«не важно какой станции» сразу после явной линии засчитывается «понятым» —
    регрессия исходного бага: раньше именно этот хвост оставался в warnings.
    """
    from app.parsing.rules.station_class import extract_station_class_requirements

    text = "рядом с МЦД не важно какой станции и округа, до 15 млн"
    reqs, spans = extract_station_class_requirements(text)
    assert [r.line_prefix for r in reqs] == ["МЦД"]

    consumed_text = "".join(text[s:e] for s, e in spans)
    assert "не важно какой станции" in consumed_text


@pytest.mark.parametrize(
    "text",
    [
        "недалеко от любой станции метро",
        "у любого метро",
        "рядом с метро не важно каким",
        "рядом с метро не важно какой станции",
    ],
)
def test_extract_station_class_any_metro_station(text: str):
    """Явное «любая станция»/«не важно» без указания класса → line_prefix «метро»."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, spans = extract_station_class_requirements(text)
    assert [r.line_prefix for r in reqs] == ["метро"]
    assert spans


def test_extract_station_class_no_false_positive_named_station():
    """«у метро Аэропорт» называет конкретную станцию — это работа
    app.parsing.entity_match, а не этого правила. Ложных срабатываний нет.
    """
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, spans = extract_station_class_requirements("хочу двушку у метро Аэропорт до 15 млн")
    assert reqs == []
    assert spans == []


def test_extract_station_class_no_false_positive_plain_metro():
    """«у метро» без какого-либо уточнения («любой»/«не важно») тоже не матчим —
    неотличимо от обычной связки перед именем станции."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, _ = extract_station_class_requirements("квартира у метро подешевле")
    assert reqs == []


def test_station_class_flows_into_criteria():
    """apply_rules прокидывает требование в Criteria.station_class_requirements."""
    from app.parsing.rules import apply_rules

    outcome = apply_rules("двушка рядом с МЦД не важно какой станции")
    assert len(outcome.criteria.station_class_requirements) == 1
    assert outcome.criteria.station_class_requirements[0].line_prefix == "МЦД"


def test_station_class_bug_regression_no_warning_leftover():
    """Регрессия исходного бага: «рядом с МЦД не важно какой станции» больше не
    попадает в warnings как нераспознанный текст."""
    from app.parsing.parser import parse

    result = parse("Нужна двушка рядом с МЦД не важно какой станции и округа, до 15 млн")
    assert any(req.line_prefix == "МЦД" for req in result.criteria.station_class_requirements)
    assert not any("не важно какой станции" in w for w in result.warnings)


def test_station_class_numbered_line_does_not_leak_into_price_e2e():
    """End-to-end регрессия основного бага (Milestone AI-16): «у МЦД-3 до 12
    млн» больше не даёт ложный ``price_min`` из номера линии. «МЦД-3»
    полностью съедается ``station_class.py`` (а не ценовым правилом), поэтому
    warnings пустые.
    """
    from app.parsing.parser import parse

    result = parse("однушка у МЦД-3 до 12 млн")
    assert result.criteria.price_min is None
    assert result.criteria.price_max == 12_000_000
    assert [req.line_prefix for req in result.criteria.station_class_requirements] == ["МЦД-3"]
    assert result.warnings == []


# --- Разговорные названия линий: цвета/номера/прозвища (Milestone AI-17) ----
#
# Живой баг: «Нужна двушка в районе коричневой ветки» терял локационную часть
# целиком в warnings и зря уходил в ИИ (упавший на 429), хотя задача решается
# полностью детерминированно — не хватало только словаря разговорных названий
# линий (см. docstring app/parsing/rules/station_class.py).


@pytest.mark.parametrize(
    ("text", "expected_lines"),
    [
        ("двушка в районе коричневой ветки", ["Кольцевая"]),
        ("у зелёной ветки", ["Замоскворецкая"]),
        ("рядом с оранжевой линией", ["Калужско-Рижская"]),
        ("квартира у синей ветки", ["Арбатско-Покровская"]),
        ("квартира у голубой ветки", ["Филёвская"]),
        ("квартира рядом с красной веткой", ["Сокольническая"]),
        ("квартира у фиолетовой линии", ["Таганско-Краснопресненская"]),
        ("квартира у серой ветки", ["Серпуховско-Тимирязевская"]),
        ("квартира у салатовой ветки", ["Люблинско-Дмитровская"]),
        ("квартира у розовой ветки", ["Некрасовская"]),
        ("квартира у тёмно-зелёной ветки", ["Троицкая"]),
        ("квартира у светло-зелёной линии", ["Люблинско-Дмитровская"]),
        # Обратный порядок слов («носитель + прилагательное»).
        ("квартира у ветки коричневой", ["Кольцевая"]),
    ],
)
def test_extract_station_class_colors(text: str, expected_lines: list[str]):
    """Цвета линий (и их склонения) со словом-носителем «ветка»/«линия»."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, spans = extract_station_class_requirements(text)
    assert [r.line_prefix for r in reqs] == expected_lines
    assert spans


def test_extract_station_class_yellow_covers_both_lines():
    """«жёлтая ветка» — разговорное имя бывшей Калининско-Солнцевской, в
    metro.json разложенной на «Калининская» и «Солнцевская» — покрывает обе
    линии (OR), одним «понятым» фрагментом.
    """
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, spans = extract_station_class_requirements("однушка у жёлтой ветки")
    assert {r.line_prefix for r in reqs} == {"Калининская", "Солнцевская"}
    assert len(reqs) == 2
    assert len(spans) == 1


@pytest.mark.parametrize(
    ("text", "expected_lines"),
    [
        ("квартира у первой ветки", ["Сокольническая"]),
        ("квартира у второй линии", ["Замоскворецкая"]),
        ("квартира у третьей ветки", ["Арбатско-Покровская"]),
        ("квартира у четвёртой ветки", ["Филёвская"]),
        ("квартира у пятой ветки", ["Кольцевая"]),
        ("квартира у шестой линии", ["Калужско-Рижская"]),
        ("квартира у седьмой ветки", ["Таганско-Краснопресненская"]),
        ("квартира у девятой ветки", ["Серпуховско-Тимирязевская"]),
        ("квартира у десятой ветки", ["Люблинско-Дмитровская"]),
    ],
)
def test_extract_station_class_ordinals(text: str, expected_lines: list[str]):
    """Порядковые номера линий словами («первая», «пятая» и т.д.)."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, _ = extract_station_class_requirements(text)
    assert [r.line_prefix for r in reqs] == expected_lines


def test_extract_station_class_eighth_covers_both_lines():
    """«восьмая ветка» — номерной синоним «жёлтой» — тоже покрывает обе линии."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, _ = extract_station_class_requirements("квартира у восьмой ветки")
    assert {r.line_prefix for r in reqs} == {"Калининская", "Солнцевская"}


@pytest.mark.parametrize(
    ("text", "expected_lines"),
    [
        ("квартира на кольце до 20 млн", ["Кольцевая"]),
        ("квартира у кольцевой", ["Кольцевая"]),
        ("квартира на большом кольце", ["Большая кольцевая"]),
        ("квартира у большой кольцевой", ["Большая кольцевая"]),
        ("квартира у БКЛ", ["Большая кольцевая"]),
        ("рядом с центральным кольцом", ["МЦК"]),
        ("квартира у диаметров", ["МЦД"]),
    ],
)
def test_extract_station_class_nicknames(text: str, expected_lines: list[str]):
    """Прозвища («кольцо», «БКЛ», «центральное кольцо», «диаметры»)."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, spans = extract_station_class_requirements(text)
    assert [r.line_prefix for r in reqs] == expected_lines
    assert spans


@pytest.mark.parametrize(
    "text",
    [
        "зелёный двор",
        "зелёная зона",
        "коричневый дом",
        "у синего дома",
        "жёлтые окна",
        "квартира в зелёном районе",
        "квартира у кольцевой дороги",
        "квартира у большой кольцевой автодороги",
    ],
)
def test_extract_station_class_no_false_positive_color_without_carrier(text: str):
    """Цвет/прозвище без слова-носителя «ветка/линия» (или МКАД-контекст
    «кольцевая дорога») ничего не даёт — не додумываем линию по одному цвету.
    """
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, spans = extract_station_class_requirements(text)
    assert reqs == []
    assert spans == []


def test_station_class_bug_regression_brown_line_e2e():
    """Регрессия исходного бага: «Нужна двушка в районе коричневой ветки»
    больше не теряет локационную часть в warnings, комнатность сохраняется.
    """
    from app.parsing.parser import parse

    result = parse("Нужна двушка в районе коричневой ветки")
    assert result.criteria.rooms == [Rooms.TWO]
    assert [r.line_prefix for r in result.criteria.station_class_requirements] == ["Кольцевая"]
    assert result.warnings == []


# --- Дистанция до станции класса (Milestone AI-19) --------------------------
#
# «Предохранитель без провода»: StationClassRequirement.max_distance_m было
# объявлено и уже учитывалось в app/geo/candidates.py (жёсткая отсечка,
# отключение фолбэка AI-18), но regex-парсинг дистанции не был написан — живой
# прогон «квартира у МЦД в 500 метрах» терял «в 500 метрах» в warnings и
# сужал выдачу по дефолтному радиусу 1500 м вместо заданных 500. Тот же класс
# дефекта, что уже разбирался у POIRequirement.max_distance_m (см. CLAUDE.md,
# «Аудит тихих потерь»).


@pytest.mark.parametrize(
    ("text", "expected_line", "expected_distance_m"),
    [
        ("у МЦД в 500 метрах", "МЦД", 500),
        ("рядом с МЦД не дальше 800 м", "МЦД", 800),
        ("у коричневой ветки в 1 км", "Кольцевая", 1000),
        ("в пределах 700 метров от МЦД-3", "МЦД-3", 700),
    ],
)
def test_extract_station_class_distance_parsed(
    text: str, expected_line: str, expected_distance_m: int
):
    """Явная дистанция после/до линии заполняет max_distance_m (закрытие бага
    «предохранитель без провода», по аналогии с POIRequirement)."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, spans = extract_station_class_requirements(text)
    assert [r.line_prefix for r in reqs] == [expected_line]
    assert reqs[0].max_distance_m == expected_distance_m
    assert spans


def test_extract_station_class_walking_distance_no_hard_cutoff():
    """«в пешей доступности» — мягкое пожелание, не жёсткая отсечка: фраза
    консьюмится (не остаётся в warnings), но max_distance_m НЕ заполняется —
    иначе это сломало бы фолбэк на ближайшие ЖК (Milestone AI-18), который
    отключается только при явной пользовательской дистанции.
    """
    from app.parsing.rules.station_class import extract_station_class_requirements

    text = "у зелёной ветки в пешей доступности"
    reqs, spans = extract_station_class_requirements(text)
    assert [r.line_prefix for r in reqs] == ["Замоскворецкая"]
    assert reqs[0].max_distance_m is None
    consumed = "".join(text[s:e] for s, e in spans)
    assert "в пешей доступности" in consumed


def test_station_class_distance_bug_regression_e2e():
    """Ровно кейс из описания бага: «квартира у МЦД в 500 метрах» больше не
    теряет дистанцию и не оставляет фрагмент в warnings."""
    from app.parsing.parser import parse

    result = parse("квартира у МЦД в 500 метрах")
    assert len(result.criteria.station_class_requirements) == 1
    assert result.criteria.station_class_requirements[0].line_prefix == "МЦД"
    assert result.criteria.station_class_requirements[0].max_distance_m == 500
    assert result.warnings == []


def test_extract_station_class_distance_does_not_steal_price():
    """«двушка у МЦД до 15 млн» — «15 млн» это цена, не дистанция: у станции
    класса дистанция не указана, а ценовое правило должно сохранить свой спан
    (регрессия многократно проверяемого в проекте класса багов «одно число —
    два смысла», см. Milestone AI-16)."""
    from app.parsing.parser import parse
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, _spans = extract_station_class_requirements("двушка у МЦД до 15 млн")
    assert [r.line_prefix for r in reqs] == ["МЦД"]
    assert reqs[0].max_distance_m is None

    result = parse("двушка у МЦД до 15 млн")
    assert result.criteria.price_max == 15_000_000
    assert result.warnings == []


def test_extract_station_class_distance_does_not_steal_area():
    """«однушка у МЦД от 60 метров» — это ПЛОЩАДЬ (area_min=60), а не радиус:
    синтаксис расстояния требует «в X метрах»/«не дальше X м», а не голое
    «от X метров» (это синтаксис area.py). Самый опасный случай — обе величины
    измеряются в метрах, поэтому проверяем оба направления явно."""
    from app.parsing.parser import parse
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, _spans = extract_station_class_requirements("однушка у МЦД от 60 метров")
    assert [r.line_prefix for r in reqs] == ["МЦД"]
    assert reqs[0].max_distance_m is None

    result = parse("однушка у МЦД от 60 метров")
    assert result.criteria.area_min == 60
    assert result.criteria.station_class_requirements[0].max_distance_m is None
    assert result.warnings == []


def test_extract_station_class_distance_does_not_steal_floor():
    """«квартира у МЦД-3 на 5 этаже» — этаж не должен становиться дистанцией.

    ``extract_floor`` не разбирает голое «на N этаже» без ключевого слова
    диапазона/границы (известное, отдельное от этой задачи ограничение — не
    относится к дистанции станций), поэтому здесь проверяем только то, что
    касается самой находки: линия распознана без дистанции, «5» не попало
    в ``max_distance_m``, а спан станции не поглотил «на 5 этаже».
    """
    from app.parsing.rules.station_class import extract_station_class_requirements

    text = "квартира у МЦД-3 на 5 этаже"
    reqs, spans = extract_station_class_requirements(text)
    assert [r.line_prefix for r in reqs] == ["МЦД-3"]
    assert reqs[0].max_distance_m is None
    assert "на 5 этаже" not in "".join(text[s:e] for s, e in spans)


# --- Единый маркер близости (Milestone AI-20, Фикс 1) ------------------------
#
# Раньше «недалеко от»/«около»/«возле»/«неподалёку от»/«вблизи» были ПОЛНО
# перечислены только в rules/landmark.py — TRIGGERS в entity_match и SUFFIX в
# rules/poi.py были независимыми неполными списками той же семантики. Общий
# ``core._PROXIMITY_MARKER`` закрывает класс дефекта разом во всех местах.


def test_poi_suffix_recognizes_bare_nedaleko():
    """SUFFIX теперь понимает «недалеко» (без «от») как суффиксный маркер
    близости — раньше список ограничивался «поблизости»/«неподалеку»/
    «неподалёку»/«близко»/«рядом с ним», «недалеко» отсутствовал."""
    from app.parsing.rules.poi import extract_poi_requirements

    text = "школа недалеко"
    poi_reqs, _center, spans = extract_poi_requirements(text)
    assert len(poi_reqs) == 1
    consumed = "".join(text[s:e] for s, e in spans)
    assert consumed == text


# --- Гомоглифы латиница→кириллица (Milestone AI-20, Фикс 2) ------------------
#
# Пользователь может случайно печатать смешанным алфавитом (неправильная
# раскладка): «двушкa» — последняя буква латинская, визуально неотличима от
# кириллической «а». ``core._normalize`` сохраняет длину строки (инвариант
# «индексы спанов валидны для исходного текста») — гомоглифы 1:1 это позволяют.

_LATIN_A = "a"
_LATIN_C = "c"
_LATIN_E = "e"
_LATIN_O = "o"


def test_normalize_maps_latin_confusables_preserving_length():
    from app.parsing.rules.core import _normalize

    text = f"двушк{_LATIN_A} у м{_LATIN_E}тр{_LATIN_O}"
    norm = _normalize(text)
    assert len(norm) == len(text)
    assert norm == "двушка у метро"


def test_apply_rules_recognizes_rooms_with_latin_homoglyph():
    """«двушкa» (последняя буква — латинская 'a') по-прежнему распознаётся как
    Rooms.TWO."""
    text = f"двушк{_LATIN_A}"
    outcome = apply_rules(text)
    assert outcome.criteria.rooms == [Rooms.TWO]


def test_apply_rules_poi_prefix_recognizes_latin_c():
    """Латинская «c» перед POI («c новым детсадом») больше не теряется в
    warnings — регрессия контрольного кейса Milestone AI-20."""
    from app.geo.poi import POICategory

    text = f"{_LATIN_C} новым детсадом"
    outcome = apply_rules(text)
    assert len(outcome.criteria.poi_requirements) == 1
    assert outcome.criteria.poi_requirements[0].category == POICategory.KINDERGARTEN
    assert outcome.criteria.poi_requirements[0].only_new is True
    consumed = "".join(text[s:e] for s, e in outcome.consumed)
    assert consumed == text


# --- Официальные имена линий метро со словом-носителем (Milestone AI-21) -----
#
# Живой баг: «Нужна двушка в районе Троицкой ветки» — AI-17 покрыл
# цвета/номера/прозвища, но не прямые имена линий. «Троицкой» уезжало в
# fuzzy-матч округа «Троицкий АО», «ветки» — в warnings и в option_candidates
# (паразитный вызов ИИ), а гейт 2 при пустых семантических требованиях выливал
# в blocks весь шорт-лист. Имена линий берутся из metro.json (RefEntry.line) —
# не дублируем справочник строками в коде.


@pytest.mark.parametrize(
    ("text", "expected_line"),
    [
        ("в районе Троицкой ветки", "Троицкая"),
        ("у Сокольнической линии", "Сокольническая"),
        ("рядом с Арбатско-Покровской веткой", "Арбатско-Покровская"),
        ("квартира на Люблинско-Дмитровской линии", "Люблинско-Дмитровская"),
    ],
)
def test_official_line_name_with_carrier(text: str, expected_line: str):
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, spans = extract_station_class_requirements(text)
    assert [r.line_prefix for r in reqs] == [expected_line]
    assert spans, "фрагмент линии должен быть засчитан понятым"


def test_official_line_name_requires_carrier():
    """«в районе Троицка» (город) без слова-носителя — НЕ класс станций."""
    from app.parsing.rules.station_class import extract_station_class_requirements

    reqs, _spans = extract_station_class_requirements("квартира в районе Троицка")
    assert reqs == []


def test_official_line_name_e2e_troitskaya():
    """Регрессия исходного бага на уровне parse(): линия распознана, округ
    «Троицкий АО» НЕ матчится ложно, «ветки» не остаётся в warnings."""
    from app.parsing.parser import parse

    result = parse("Нужна двушка в районе Троицкой ветки")
    assert [r.line_prefix for r in result.criteria.station_class_requirements] == ["Троицкая"]
    assert result.criteria.counties == []
    assert result.warnings == []
    assert result.option_candidates == []
    assert result.criteria.rooms == [Rooms.TWO]
