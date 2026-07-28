"""Число внутри названия сущности не должно становиться границей диапазона.

Класс дефекта — **сфабрикованный фильтр**: программа дописывает пользователю
условие, которого он не просил. «Квартиру в Руставели 14 до 21 млн» давало
``priceFrom=14000000`` — число из названия ЖК прочиталось как нижняя граница
цены, потому что regex-правила отрабатывали ДО матчинга сущностей и спана ЖК
в момент их работы ещё не существовало.

Проверяется механизм, а не конкретный ЖК: тесты параметризованы по всем
названиям из справочника, содержащим число.
"""

import json
import re
from pathlib import Path

import pytest

from app.parsing.parser import parse

_COMPLEXES = Path("app/reference/complexes.json")

#: ЖК, который проигрывает матчинг сущностей ДРУГОМУ дефекту и потому не
#: защищается этим механизмом: «Большая Академическая 85» вытесняется станцией
#: метро «Академическая» (score 100.1 против 95.1) и в matches не попадает
#: вовсе — резервировать нечего. Корень другой (ранжирование сущностей, не
#: коллизия спанов), поэтому чинится отдельно; xfail строгий, чтобы починка
#: там не осталась незамеченной здесь.
_SHADOWED_BY_METRO = "Большая Академическая 85"


def _numbered_complexes() -> list[str]:
    entries = json.loads(_COMPLEXES.read_text(encoding="utf-8"))
    return [e["name"] for e in entries if re.search(r"\d", e["name"])]


def _numbered_complexes_ranged() -> list:
    """То же, но с xfail на ЖК, который не защищён по чужой причине (см. выше)."""
    return [
        pytest.param(
            name,
            marks=pytest.mark.xfail(
                strict=True,
                reason="ЖК вытеснен станцией метро «Академическая» — дефект ранжирования сущностей",
            ),
        )
        if name == _SHADOWED_BY_METRO
        else name
        for name in _numbered_complexes()
    ]


def _public(text: str) -> dict:
    return parse(text).criteria.to_public_dict()


# --- Заявленный критерий готовности -----------------------------------------


def test_price_min_not_fabricated_from_complex_number():
    """«Руставели 14 до 21 млн»: 14 — часть названия, а не нижняя граница."""
    result = _public("Квартиру в Руставели 14 до 21 млн")

    assert "price_min" not in result
    assert result["price_max"] == 21_000_000
    assert "Руставели 14" in result["complexes"]


def test_price_min_kept_when_number_named_twice():
    """«Руставели 14 от 14 до 21 млн»: второе 14 названо отдельно — это диапазон."""
    result = _public("Квартиру в Руставели 14 от 14 до 21 млн")

    assert result["price_min"] == 14_000_000
    assert result["price_max"] == 21_000_000
    assert "Руставели 14" in result["complexes"]


# --- Зеркала: площадь и этаж ------------------------------------------------


def test_area_min_not_fabricated_from_complex_number():
    result = _public("Барклая 6 до 100 метров")

    assert "area_min" not in result
    assert result["area_max"] == 100.0
    assert "Барклая 6" in result["complexes"]


def test_area_min_kept_when_number_named_twice():
    result = _public("Барклая 6 от 60 до 100 метров")

    assert result["area_min"] == 60.0
    assert result["area_max"] == 100.0
    assert "Барклая 6" in result["complexes"]


def test_floor_not_fabricated_from_complex_number():
    result = _public("Кольская 8 до 15 этажа")

    assert "floor_min" not in result
    assert result["floor_max"] == 15
    assert "Кольская 8" in result["complexes"]


def test_floor_min_kept_when_named_separately():
    result = _public("Кольская 8 этаж от 5")

    assert result["floor_min"] == 5
    assert "floor_max" not in result
    assert "Кольская 8" in result["complexes"]


# --- Механизм, а не заплатка на три названия --------------------------------


@pytest.mark.parametrize("name", _numbered_complexes())
def test_numbered_complex_alone_fabricates_nothing(name: str):
    """Голое название ЖК с числом не рождает ни одного нелокационного фильтра."""
    result = _public(name)
    location_keys = {"complexes", "metro", "districts", "counties"}

    assert not (set(result) - location_keys), f"{name} → {result}"


@pytest.mark.parametrize("name", _numbered_complexes_ranged())
def test_numbered_complex_with_price_ceiling_has_no_lower_bound(name: str):
    """«<ЖК с числом> до 30 млн» — только потолок цены, нижней границы нет."""
    result = _public(f"{name} до 30 млн")

    assert result.get("price_max") == 30_000_000, f"{name} → {result}"
    assert "price_min" not in result, f"{name} → {result}"


@pytest.mark.parametrize("name", _numbered_complexes_ranged())
def test_numbered_complex_with_area_ceiling_has_no_lower_bound(name: str):
    """«<ЖК с числом> до 120 метров» — только верхняя граница площади."""
    result = _public(f"{name} до 120 метров")

    assert result.get("area_max") == 120.0, f"{name} → {result}"
    assert "area_min" not in result, f"{name} → {result}"
