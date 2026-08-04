"""Регресс на класс «сфабрикованный фильтр»: фильтр не теряется, а ДОБАВЛЯЕТСЯ лишний.

Оба QA-корпуса такой дефект принципиально не видят (см. CLAUDE.md, «QA-харнесс»):
сводная метрика «фильтров на запрос» от лишнего фильтра только «улучшается».
Поэтому здесь — точечные утверждения, а не пороги.

Разбор обоих дефектов — ``docs/parsing-rationale.md``.
"""

import pytest

from app.parsing.entity_match import match_entities
from app.parsing.parser import parse


def _groups(text: str) -> set[str]:
    return set(parse(text).criteria.option_groups)


class TestBathroomModifierNotFabricated:
    """Д5: «гостевой/второй санузел» — про ЧИСЛО санузлов, а не про планировку.

    ``throughbathroom`` («Сквозной санузел») и ``manybathrooms`` («Два и более
    санузла») различаются ТОЛЬКО прилагательным, и WRatio их не различает:
    «гостевой санузел» ~ «сквозной санузел» = 75.00 — ровно на пороге
    ``TRIGGERED_SCORE_THRESHOLD``.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "гостевой санузел",
            "второй санузел",
            "отдельный санузел",
            "дополнительный санузел",
            "квартира с гостевым санузлом",
            "квартира со вторым санузлом",
        ],
    )
    def test_guest_bathroom_is_manybathrooms_not_throughbathroom(self, text: str) -> None:
        groups = _groups(text)
        assert "throughbathroom" not in groups, f"сфабрикованный фильтр планировки для «{text}»"
        assert "manybathrooms" in groups

    @pytest.mark.parametrize("text", ["сквозной санузел", "квартира со сквозным санузлом"])
    def test_throughbathroom_true_positive_preserved(self, text: str) -> None:
        groups = _groups(text)
        assert "throughbathroom" in groups
        assert "manybathrooms" not in groups

    @pytest.mark.parametrize(
        "text",
        ["два санузла", "2 санузла", "квартира с двумя санузлами", "несколько санузлов"],
    )
    def test_manybathrooms_true_positive_preserved(self, text: str) -> None:
        assert "manybathrooms" in _groups(text)

    def test_master_bedroom_case_from_real_run(self) -> None:
        """Реальный запрос: обе опции честные, ни одной сфабрикованной."""
        text = "мастер-спальня с отдельным санузлом и второй гостевой санузел"
        result = parse(text)
        groups = set(result.criteria.option_groups)
        assert "masterbedroom" in groups
        assert "manybathrooms" in groups
        assert "throughbathroom" not in groups

    def test_bare_bathroom_word_still_creates_no_filter(self) -> None:
        """Однословное «санузлом» без прилагательного — по-прежнему НЕ фильтр.

        Защита из Milestone AI-* («отдельный санузел» ложно матчился на «Два и
        более санузла») остаётся в силе с другой стороны: у окна нет
        прилагательного-различителя, значит выбрать между «сквозным» и «двумя»
        не на чем — и молчаливая подмена смысла хуже честной потери (инвариант 1).
        """
        result = parse("квартира с санузлом")
        assert result.criteria.option_groups == []
        assert result.warnings, "фрагмент обязан остаться в warnings (инвариант 1)"

    def test_unknown_bathroom_modifier_is_not_silently_mapped(self) -> None:
        """Прилагательное, которого нет ни в одном алиасе, фильтра не создаёт."""
        result = parse("квартира с розовым санузлом")
        assert result.criteria.option_groups == []
        assert result.warnings

    def test_second_floor_does_not_leak_into_bathroom_alias(self) -> None:
        """Новый алиас «второй санузел» не должен ловить «второй этаж»."""
        assert "manybathrooms" not in _groups("квартира на втором этаже")

    def test_matcher_level_guest_bathroom(self) -> None:
        matches, _warnings = match_entities("квартира с гостевым санузлом")
        slugs = {m.entity.slug for m in matches}
        assert "throughbathroom" not in slugs
        assert "manybathrooms" in slugs


class TestKitchenLivingRoomArea:
    """Д6: «кухня-гостиная от 20 м²» — площадь КУХНИ, а не квартиры."""

    def test_kitchen_living_room_alone(self) -> None:
        criteria = parse("кухня-гостиная от 20 м²").criteria
        assert criteria.area_kitchen_min == 20.0
        assert criteria.area_min is None, "сфабрикованная общая площадь"

    def test_kitchen_living_room_with_total_area(self) -> None:
        result = parse("площадью от 75 м² с кухней-гостиной от 20 м²")
        assert result.criteria.area_min == 75.0
        assert result.criteria.area_kitchen_min == 20.0
        assert result.warnings == []

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("кухня-столовая от 18 квадратов", 18.0),
            ("с кухней-гостиной не меньше 22 м²", 22.0),
            ("кухня гостиная от 20 м²", 20.0),
        ],
    )
    def test_compound_kitchen_forms(self, text: str, expected: float) -> None:
        criteria = parse(text).criteria
        assert criteria.area_kitchen_min == expected
        assert criteria.area_min is None

    def test_plain_kitchen_unchanged(self) -> None:
        criteria = parse("с кухней от 20 м²").criteria
        assert criteria.area_kitchen_min == 20.0
        assert criteria.area_min is None


class TestAreaFirstWinsIsNotSilent:
    """Дефект №7: значение, отбитое защитой «первый выигрывает», не молчит.

    Спан списывается в любом случае (иначе число подберёт другое правило), поэтому
    без явного warning'а фрагмент исчезал бесследно — нарушение инварианта 1.
    """

    def test_second_area_min_produces_lost_warning(self) -> None:
        result = parse("площадью от 75 м² и от 60 м²")
        assert result.criteria.area_min == 75.0
        assert any("60" in w for w in result.warnings), result.warnings

    def test_lost_warning_is_tagged_lost(self) -> None:
        from app.warnings import WarningCategory

        result = parse("площадью от 75 м² и от 60 м²")
        tagged = [w for w in result.warnings if "60" in w]
        assert tagged
        assert all(getattr(w, "category", None) is WarningCategory.LOST for w in tagged)

    def test_second_kitchen_area_produces_lost_warning(self) -> None:
        result = parse("кухня от 12 м² и кухня от 9 м²")
        assert result.criteria.area_kitchen_min == 12.0
        assert any("9" in w for w in result.warnings), result.warnings
