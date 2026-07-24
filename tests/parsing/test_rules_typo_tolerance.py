"""Терпимость правил к опечаткам/разговорным формам (Milestone «ведро A»).

Мелкие детерминированные добивки — фразы, которые раньше молча уходили в
warnings (см. аудит `scripts/parse_audit.py`): опечатки падежа, разговорные
синонимы комнатности, альтернация «либо», позитивный «первый этаж». Всё —
без ИИ; здесь же зафиксированы анти-регрессии (отрицания и ложные срабатывания).
"""

import pytest

from app.parsing.rules.area import extract_area
from app.parsing.rules.finish import extract_finish
from app.parsing.rules.floor import extract_floor
from app.parsing.rules.misc import extract_sort
from app.parsing.rules.rooms import extract_rooms
from app.parsing.schema import Finish, Rooms, Sort


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("падешевле", Sort.PRICE_ASC),  # опечатка о→а
        ("падороже", Sort.PRICE_DESC),
        ("подешевле", Sort.PRICE_ASC),  # каноничная форма не сломана
        ("подороже", Sort.PRICE_DESC),
    ],
)
def test_sort_typo_tolerance(text: str, expected: Sort) -> None:
    assert extract_sort(text)[0] == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("хочу трёху", [Rooms.THREE_PLUS]),  # разговорное «трёха»
        ("трёха", [Rooms.THREE_PLUS]),
        ("трёшку", [Rooms.THREE_PLUS]),  # уже работало — не сломано
        ("1 либо 2 комнатную", [Rooms.ONE, Rooms.TWO]),  # альтернация «либо»
    ],
)
def test_rooms_colloquial_and_libo(text: str, expected: list[Rooms]) -> None:
    assert extract_rooms(text)[0] == expected


@pytest.mark.parametrize(
    "text",
    [
        "не трёху",  # отрицание разговорной формы
        "у трёх подъездов",  # числительное «трёх» — не комнатность
    ],
)
def test_rooms_troha_no_false_positive(text: str) -> None:
    assert extract_rooms(text)[0] == []


def test_finish_padezh_typo() -> None:
    assert extract_finish("трёшка с отделкай")[0] == [Finish.READY]


def test_finish_negation_not_broken() -> None:
    assert extract_finish("без отделки")[0] == [Finish.NONE]


def test_kitchen_area_at_typo() -> None:
    facts = extract_area("кухня ат 10 метров")[0]
    assert facts.area_kitchen_min == 10.0
    assert facts.area_min is None  # не утекло в общую площадь


def test_floor_first_positive() -> None:
    facts = extract_floor("первый этаж")[0]
    assert facts.floor_min == 1
    assert facts.floor_max == 1
    assert facts.not_first_floor is False


@pytest.mark.parametrize(
    "text",
    [
        "первый этаж не предлагать",  # постфиксное отрицание
        "не первый этаж",  # префиксное отрицание (было раньше)
    ],
)
def test_floor_first_negations(text: str) -> None:
    facts = extract_floor(text)[0]
    assert facts.not_first_floor is True
    assert facts.floor_min is None
    assert facts.floor_max is None


def test_floor_first_no_false_positive() -> None:
    # «первый взнос» (ипотека) не должен ставить этаж.
    facts = extract_floor("первый взнос по ипотеке")[0]
    assert facts.floor_min is None
    assert facts.floor_max is None
