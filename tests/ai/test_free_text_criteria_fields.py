"""Ведро C: резолв остатка в справочно-валидируемые ПОЛЯ Criteria.

До этого экстрактор свободного текста умел только скаляры/булевы/``rooms``
и ориентиры. Отделка и POI оставались исключительно на детерминированных
путях, поэтому любая незнакомая формулировка («отделка готовая» до правки
регекса, «до сада» и т.п.) уходила в ``option_candidates`` — то есть
сверялась со справочником ОПЦИЙ, где отделки нет физически. Модель фразу
видела и молча не могла ничего с ней сделать.

Рубеж доверия здесь тот же, что у :func:`sanitize_landmark_resolution`:
принимаем не строку модели, а значение энума, и только если ``phrase``
дословно входит в переданные модели фрагменты.
"""

from unittest.mock import patch

import pytest

from app.ai.enrichment import (
    _UNRECOGNIZED_SUFFIX,
    _apply_free_text_answer,
    enrich,
    resolve_free_text_criteria,
    sanitize_finish_resolution,
    sanitize_poi_resolution,
)
from app.ai.schema import FinishMatch, FreeTextCriteriaAnswer, POIMatch
from app.config import get_settings
from app.geo.poi import POICategory
from app.parsing.schema import Criteria, Finish


@pytest.fixture
def mock_settings():
    settings = get_settings()
    original_enabled = settings.AI_ENRICHMENT_ENABLED
    original_key = settings.ANTHROPIC_API_KEY
    settings.AI_ENRICHMENT_ENABLED = True
    settings.ANTHROPIC_API_KEY = "sk-test"
    yield settings
    settings.AI_ENRICHMENT_ENABLED = original_enabled
    settings.ANTHROPIC_API_KEY = original_key


# ---------------------------------------------------------------------------
# Отделка
# ---------------------------------------------------------------------------


def test_sanitize_finish_accepts_enum_value_for_known_fragment() -> None:
    answer = FreeTextCriteriaAnswer(
        finish=[FinishMatch(phrase="отделка топовая", finish=Finish.READY)]
    )

    assert sanitize_finish_resolution(answer, ["отделка топовая"]) == [Finish.READY]


def test_sanitize_finish_rejects_phrase_outside_fragments() -> None:
    """Реальное значение энума, привязанное к произвольной фразе, не проходит.

    Иначе на любом шуме в остатке молча появился бы фильтр отделки.
    """
    answer = FreeTextCriteriaAnswer(
        finish=[FinishMatch(phrase="совершенно другая фраза", finish=Finish.READY)]
    )

    assert sanitize_finish_resolution(answer, ["отделка топовая"]) == []


def test_sanitize_finish_skips_null_and_dedupes() -> None:
    answer = FreeTextCriteriaAnswer(
        finish=[
            FinishMatch(phrase="а", finish=None),
            FinishMatch(phrase="б", finish=Finish.READY),
            FinishMatch(phrase="в", finish=Finish.READY),
        ]
    )

    assert sanitize_finish_resolution(answer, ["а", "б", "в"]) == [Finish.READY]


# ---------------------------------------------------------------------------
# POI
# ---------------------------------------------------------------------------


def test_sanitize_poi_builds_requirement_from_known_fragment() -> None:
    answer = FreeTextCriteriaAnswer(
        poi=[
            POIMatch(
                phrase="до ясель недалеко",
                category=POICategory.KINDERGARTEN,
                max_distance_m=600,
                only_new=True,
            )
        ]
    )

    reqs = sanitize_poi_resolution(answer, ["до ясель недалеко"])

    assert len(reqs) == 1
    assert reqs[0].category is POICategory.KINDERGARTEN
    assert reqs[0].raw_phrase == "до ясель недалеко"
    assert reqs[0].max_distance_m == 600
    assert reqs[0].only_new is True


def test_sanitize_poi_rejects_phrase_outside_fragments() -> None:
    answer = FreeTextCriteriaAnswer(
        poi=[POIMatch(phrase="выдуманная фраза", category=POICategory.SCHOOL)]
    )

    assert sanitize_poi_resolution(answer, ["до ясель недалеко"]) == []


def test_sanitize_poi_drops_implausible_distance_but_keeps_requirement() -> None:
    """Абсурдная дистанция гасится в None, само требование остаётся.

    POI-требование по природе про пешую доступность: 300 км — не отсечка, а
    её отсутствие. Выбрасывать всё требование нельзя (потеряли бы факт), но и
    писать в фильтр такое число нельзя — он перестал бы что-либо сужать.
    """
    answer = FreeTextCriteriaAnswer(
        poi=[
            POIMatch(
                phrase="до ясель недалеко",
                category=POICategory.KINDERGARTEN,
                max_distance_m=300_000,
            )
        ]
    )

    reqs = sanitize_poi_resolution(answer, ["до ясель недалеко"])

    assert len(reqs) == 1
    assert reqs[0].max_distance_m is None


# ---------------------------------------------------------------------------
# Вливание в Criteria и снятие warning'ов
# ---------------------------------------------------------------------------


def test_apply_fills_finish_and_clears_its_warning() -> None:
    criteria = Criteria()
    fragment = "отделка топовая"
    warnings = [f"«{fragment}{_UNRECOGNIZED_SUFFIX}"]
    answer = FreeTextCriteriaAnswer(
        finish=[FinishMatch(phrase=fragment, finish=Finish.READY)],
        consumed_fragments=[fragment],
    )

    changed = _apply_free_text_answer(criteria, answer, [fragment], warnings)

    assert changed is True
    assert criteria.finish == [Finish.READY]
    assert warnings == []


def test_apply_fills_poi_and_clears_its_warning() -> None:
    criteria = Criteria()
    fragment = "до ясель недалеко"
    warnings = [f"«{fragment}{_UNRECOGNIZED_SUFFIX}"]
    answer = FreeTextCriteriaAnswer(
        poi=[POIMatch(phrase=fragment, category=POICategory.KINDERGARTEN)],
        consumed_fragments=[fragment],
    )

    changed = _apply_free_text_answer(criteria, answer, [fragment], warnings)

    assert changed is True
    assert [r.category for r in criteria.poi_requirements] == [POICategory.KINDERGARTEN]
    assert warnings == []


def test_apply_does_not_override_deterministic_finish() -> None:
    """Инвариант ведра C: модель не переопределяет уже разобранное."""
    criteria = Criteria(finish=[Finish.NONE])
    fragment = "отделка топовая"
    warnings = [f"«{fragment}{_UNRECOGNIZED_SUFFIX}"]
    answer = FreeTextCriteriaAnswer(
        finish=[FinishMatch(phrase=fragment, finish=Finish.READY)],
        consumed_fragments=[fragment],
    )

    _apply_free_text_answer(criteria, answer, [fragment], warnings)

    assert criteria.finish == [Finish.NONE]


def test_apply_keeps_warning_when_finish_match_carried_no_value() -> None:
    """Фраза отбита санитайзером → warning обязан остаться (инвариант 1).

    Модель заявила фрагмент разобранным и заодно угадала другое поле
    (``changed=True``). Без точечного исключения этого хватило бы, чтобы
    фрагмент исчез молча: ни значения отделки, ни предупреждения о нём.
    """
    criteria = Criteria()
    fragment = "отделка топовая"
    warnings = [f"«{fragment}{_UNRECOGNIZED_SUFFIX}"]
    answer = FreeTextCriteriaAnswer(
        price_max=5_000_000,
        finish=[FinishMatch(phrase=fragment, finish=None)],
        consumed_fragments=[fragment],
    )

    _apply_free_text_answer(criteria, answer, [fragment], warnings)

    assert criteria.price_max == 5_000_000
    assert criteria.finish == []
    assert warnings == [f"«{fragment}{_UNRECOGNIZED_SUFFIX}"]


def test_apply_ignores_finish_bound_to_phrase_outside_residual() -> None:
    """Реальное значение энума на выдуманной фразе в criteria не попадает.

    Снятие warning'а по ``consumed_fragments`` здесь остаётся на общем правиле
    для полей без провенанса (скаляры) — проверяем то, что защищает новый
    рубеж: лишний фильтр отделки не появляется.
    """
    criteria = Criteria()
    fragment = "отделка топовая"
    answer = FreeTextCriteriaAnswer(
        finish=[FinishMatch(phrase="фраза не из остатка", finish=Finish.READY)],
        consumed_fragments=[fragment],
    )

    changed = _apply_free_text_answer(criteria, answer, [fragment], [])

    assert criteria.finish == []
    assert changed is False


# ---------------------------------------------------------------------------
# Сквозная проводка: ветка не должна оказаться мёртвой
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_resolve_free_text_wires_finish_end_to_end(mock_extractor, mock_settings) -> None:
    """Отделка доезжает от ответа модели до criteria через реальную ветку."""
    fragment = "отделка топовая"
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        finish=[FinishMatch(phrase=fragment, finish=Finish.READY)],
        consumed_fragments=[fragment],
        explanation="отделка готовая",
    )
    criteria = Criteria()
    warnings = [f"«{fragment}{_UNRECOGNIZED_SUFFIX}"]

    outcome = await resolve_free_text_criteria(criteria, f"двушка, {fragment}", warnings, None)

    assert outcome.changed is True
    assert criteria.finish == [Finish.READY]
    assert warnings == []


@pytest.mark.asyncio
@patch("app.ai.enrichment.build_candidate_shortlist")
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_ai_added_poi_opens_gate_one(mock_extractor, mock_shortlist, mock_settings) -> None:
    """POI от модели ОБЯЗАН открывать гейт 1 и включать гео-сужение.

    Иначе требование «до ясель» осело бы в criteria и не повлияло ни на что:
    гейт схлопнул бы запрос в noop() до шорт-листа. Проверяем именно факт
    вызова шорт-листа — он и есть вход в гео-ветку.
    """
    fragment = "до ясель недалеко"
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        poi=[POIMatch(phrase=fragment, category=POICategory.KINDERGARTEN)],
        consumed_fragments=[fragment],
    )
    mock_shortlist.return_value = []
    criteria = Criteria()
    warnings = [f"«{fragment}{_UNRECOGNIZED_SUFFIX}"]

    await enrich(f"квартира, {fragment}", criteria, warnings, pool=None)

    assert [r.category for r in criteria.poi_requirements] == [POICategory.KINDERGARTEN]
    assert mock_shortlist.called


# ---------------------------------------------------------------------------
# П1: потолок дистанции обязан совпадать с радиусом кэша POI
# ---------------------------------------------------------------------------


def test_ai_poi_distance_ceiling_matches_cache_radius() -> None:
    """Иначе появляется зона-призрак: требование принято, подтвердиться нечем.

    Кэш POI собирается в радиусе POI_CACHE_RADIUS_M; за его пределами
    closest_distance_m попросту null, и никакая отсечка больше этого числа
    выполниться не может — но выглядела бы применённой.
    """
    from app.ai.enrichment import _AI_POI_MAX_DISTANCE_M
    from app.geo.poi import POI_CACHE_RADIUS_M

    assert _AI_POI_MAX_DISTANCE_M == POI_CACHE_RADIUS_M


def test_sanitize_poi_drops_distance_beyond_cache_radius() -> None:
    answer = FreeTextCriteriaAnswer(
        poi=[POIMatch(phrase="до сада", category=POICategory.KINDERGARTEN, max_distance_m=5000)]
    )

    reqs = sanitize_poi_resolution(answer, ["до сада"])

    assert len(reqs) == 1
    assert reqs[0].max_distance_m is None
