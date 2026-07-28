"""Экстрактор свободного текста → Criteria (ведро C, ослабление гейтов).

Новый ИИ-путь достаёт недостающие СКАЛЯРНЫЕ фильтры из непонятого парсером
текста. Здесь зафиксированы инварианты: детерминированный слой выигрывает
(заполняем только пустые поля), невалидное значение отбрасывается, ведро B/шум
(нет реального остатка) не жжёт вызов, снятие warning'ов по consumed_fragments,
и честный ai_used даже когда гейт 1 вернул бы noop().
"""

from unittest.mock import patch

import pytest

from app.ai.enrichment import _residual_fragments, enrich, resolve_free_text_criteria
from app.ai.prompts import build_free_text_context
from app.ai.schema import FreeTextCriteriaAnswer, LandmarkMatch
from app.config import get_settings
from app.parsing.schema import Criteria, LandmarkRequirement, Rooms, Sort

_RESIDUAL = "«{}»: не удалось распознать, не попало в ссылку"


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


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_fills_empty_scalar_and_removes_warning(mock_extractor, mock_settings):
    """Пустое поле заполняется, warning по consumed_fragment снимается."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        sort=Sort.PRICE_ASC,
        consumed_fragments=["по возрастанию стоимости"],
        explanation="сортировка по цене",
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("по возрастанию стоимости")]

    outcome = await resolve_free_text_criteria(
        criteria, "квартира по возрастанию стоимости", warnings, None
    )

    assert outcome.changed is True
    assert criteria.sort == Sort.PRICE_ASC
    assert warnings == []  # фраза снята


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_does_not_override_deterministic(mock_extractor, mock_settings):
    """Детерминированный слой выигрывает: занятое поле НЕ перетирается."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(price_max=99, consumed_fragments=["хрень"])
    criteria = Criteria(price_max=15_000_000)
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    assert criteria.price_max == 15_000_000  # не тронуто
    assert outcome.changed is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_invalid_value_rejected(mock_extractor, mock_settings):
    """Значение вне границ Criteria (price<0) отбрасывается validate_assignment."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(price_min=-5, consumed_fragments=["хрень"])
    criteria = Criteria()
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    assert criteria.price_min is None  # брак не применён
    assert outcome.changed is False
    assert warnings == [_RESIDUAL.format("хрень")]  # warning остался — ничего не разобрано


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_rooms_filled_only_if_empty(mock_extractor, mock_settings):
    """rooms заполняются только при пустом детерминированном списке."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        rooms=[Rooms.ONE, Rooms.TWO], consumed_fragments=["хрень"]
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    assert criteria.rooms == [Rooms.ONE, Rooms.TWO]
    assert outcome.changed is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_bool_flag_set(mock_extractor, mock_settings):
    """Булев флаг включается только True поверх дефолтного False."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        only_available=True, consumed_fragments=["хрень"]
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    assert criteria.only_available is True
    assert outcome.changed is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_no_residual_skips_call(mock_extractor, mock_settings):
    """Нет реального остатка (только «не поддерживается pik.ru») → модель не зовём."""
    criteria = Criteria()
    warnings = [
        "«вторичка»: вторичное жильё не поддерживается pik.ru (только новостройки) — пропущено"
    ]

    outcome = await resolve_free_text_criteria(criteria, "вторичка", warnings, None)

    mock_extractor.assert_not_called()
    assert outcome.called is False
    assert outcome.changed is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_disabled_skips_call(mock_extractor, mock_settings):
    """ИИ выключен → остаток остаётся в warnings, модель не дёргаем."""
    mock_settings.AI_ENRICHMENT_ENABLED = False
    criteria = Criteria()
    warnings = [_RESIDUAL.format("хрень")]

    outcome = await resolve_free_text_criteria(criteria, "квартира хрень", warnings, None)

    mock_extractor.assert_not_called()
    assert outcome.called is False


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_time_to_metro_safety_net(mock_extractor, mock_settings):
    """ИИ-страховка: время до метро, промахнутое детерминикой, заполняет ИИ."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        time_on_foot=12, consumed_fragments=["до метро рукой подать"]
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("до метро рукой подать")]

    outcome = await resolve_free_text_criteria(criteria, "до метро рукой подать", warnings, None)

    assert criteria.time_on_foot == 12
    assert outcome.changed is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_enrich_sets_ai_used_even_when_gate1_noop(mock_extractor, mock_settings):
    """Ключевой кейс ослабления гейтов: запрос без структурных сигналов (гейт 1
    вернул бы noop()), но экстрактор заполнил поле → ai_used честно True."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        sort=Sort.PRICE_DESC,
        consumed_fragments=["сначала элитные"],
        explanation="дорогие вперёд",
    )
    criteria = Criteria()  # ни poi/center/landmark/station — гейт 1 сработал бы
    warnings = [_RESIDUAL.format("сначала элитные")]

    result = await enrich("квартира сначала элитные", criteria, warnings)

    assert criteria.sort == Sort.PRICE_DESC  # criteria обогащён до гейта
    assert result.meta.ai_used is True  # ИИ реально повлиял
    assert result.meta.explanation == "дорогие вперёд"


# --- Ориентиры (Milestone AI-22): экстрактор вправе резолвить landmark_requirements ---
# Мотивация — живой прогон «двушку самую ближайшую к Политеху»: остаток был, ИИ
# вызывался, но по конструкции не имел права трогать ориентиры → ai_used=false.
# Инвариант 3 соблюдён: модель возвращает только slug, координаты берутся из
# landmarks.json (sanitize_landmark_resolution), выдуманные slug отбрасываются.


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_landmark_resolved_from_reference(mock_extractor, mock_settings):
    """Валидный slug → LandmarkRequirement с координатами ИЗ СПРАВОЧНИКА, не от модели."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        landmarks=[LandmarkMatch(phrase="около Бауманки", slug="bauman")],
        consumed_fragments=["около Бауманки"],
        explanation="ориентир — МГТУ",
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("около Бауманки")]

    outcome = await resolve_free_text_criteria(criteria, "двушка около Бауманки", warnings, None)

    assert outcome.changed is True
    assert len(criteria.landmark_requirements) == 1
    lm = criteria.landmark_requirements[0]
    assert lm.name == "МГТУ им. Баумана"
    assert (lm.lat, lm.lon) == (55.766043, 37.684917)  # из landmarks.json
    assert lm.category == "university"
    assert lm.raw_phrase == "около Бауманки"
    assert warnings == []


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_landmark_hallucinated_slug_rejected(mock_extractor, mock_settings):
    """Slug вне справочника — галлюцинация: не применяем, warning остаётся."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        landmarks=[LandmarkMatch(phrase="рядом с Атлантидой", slug="atlantis")],
        consumed_fragments=["рядом с Атлантидой"],
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("рядом с Атлантидой")]

    outcome = await resolve_free_text_criteria(
        criteria, "двушка рядом с Атлантидой", warnings, None
    )

    assert criteria.landmark_requirements == []
    assert outcome.changed is False
    assert warnings == [_RESIDUAL.format("рядом с Атлантидой")]


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_landmark_does_not_override_deterministic(mock_extractor, mock_settings):
    """Детерминированный слой выигрывает и здесь: непустой список не трогаем."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        landmarks=[LandmarkMatch(phrase="около Бауманки", slug="bauman")],
        consumed_fragments=["около Бауманки"],
    )
    criteria = Criteria(
        landmark_requirements=[
            LandmarkRequirement(name="МГУ им. Ломоносова", lat=55.703, lon=37.530)
        ]
    )
    warnings = [_RESIDUAL.format("около Бауманки")]

    outcome = await resolve_free_text_criteria(criteria, "двушка около Бауманки", warnings, None)

    assert [lm.name for lm in criteria.landmark_requirements] == ["МГУ им. Ломоносова"]
    assert outcome.changed is False


def test_free_text_context_includes_landmark_catalogue():
    """Модель может вернуть только slug из каталога — значит каталог надо ей отдать.
    Без этого ветка ориентиров мертва в проде (в юнит-тестах ответ замокан)."""
    context = build_free_text_context("двушка около Бауманки", Criteria(), ["около Бауманки"])

    assert "bauman" in {lm["slug"] for lm in context["landmarks"]}
    # Отдаём только name+slug, как в build_option_context: координаты модели не нужны
    # и не должны попадать в ответ (инвариант «LLM не считает дистанции»).
    assert all(set(lm) == {"name", "slug"} for lm in context["landmarks"])


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_landmark_rejected_slug_keeps_its_warning(mock_extractor, mock_settings):
    """Отбитый санитайзером ориентир НЕ снимает свой warning (инвариант 1).

    Тонкость: warning'и снимались по всем consumed_fragments, если изменилось
    ХОТЬ ОДНО поле. Значит достаточно было модели заодно угадать сортировку —
    и выдуманный ориентир исчезал молча вместе со своей фразой.
    """
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        sort=Sort.PRICE_DESC,
        landmarks=[LandmarkMatch(phrase="рядом с Атлантидой", slug="atlantis")],
        consumed_fragments=["подороже", "рядом с Атлантидой"],
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("подороже"), _RESIDUAL.format("рядом с Атлантидой")]

    outcome = await resolve_free_text_criteria(
        criteria, "двушка рядом с Атлантидой подороже", warnings, None
    )

    assert outcome.changed is True  # сортировка применена
    assert criteria.landmark_requirements == []
    assert warnings == [_RESIDUAL.format("рядом с Атлантидой")]  # фраза не потеряна


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_landmark_phrase_must_come_from_fragments(mock_extractor, mock_settings):
    """Реальный slug, привязанный к чужой фразе, — тоже недоверенный ответ.

    Иначе на любом шуме в остатке («метро рядом») модель могла бы подставить
    произвольный ориентир из каталога и молча сузить выдачу по нему.
    """
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        landmarks=[LandmarkMatch(phrase="какая-то отсебятина", slug="bauman")],
        consumed_fragments=["метро рядом"],
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("метро рядом")]

    outcome = await resolve_free_text_criteria(criteria, "двушка метро рядом", warnings, None)

    assert criteria.landmark_requirements == []
    assert outcome.changed is False
    assert warnings == [_RESIDUAL.format("метро рядом")]


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_landmark_from_ai_keeps_superlative(mock_extractor, mock_settings):
    """Суперлатив в тексте не должен теряться, когда ориентир достал ИИ.

    Детерминированное правило промахивается на части склонений («бауманке» —
    QRatio 87.5 при пороге 88), фразу добирает ИИ — и без этого признака запрос
    деградировал в «рядом» (радиус 5 км), ради ухода от которого и заведён
    nearest_only.
    """
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        landmarks=[LandmarkMatch(phrase="самую ближайшую к Бауманке", slug="bauman")],
        consumed_fragments=["самую ближайшую к Бауманке"],
    )
    criteria = Criteria()
    warnings = [_RESIDUAL.format("самую ближайшую к Бауманке")]

    await resolve_free_text_criteria(criteria, "однушка самую ближайшую к Бауманке", warnings, None)

    assert criteria.landmark_requirements[0].nearest_only is True


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_greeting_only_residual_skips_call(mock_extractor, mock_settings):
    """Приветствие — единственный остаток → вызова нет вовсе.

    Живой замер: экстрактор звался с fragments=1, и этим фрагментом было
    «Привет» — модель тратила вызов на объяснение, что приветствие не относится
    ни к одному полю. Warning при этом остаётся (инвариант 1): задача — не
    оплачивать вызов, а не спрятать шум.
    """
    criteria = Criteria()
    warnings = [_RESIDUAL.format("Привет")]

    outcome = await resolve_free_text_criteria(criteria, "Привет! Двушку до 15 млн", warnings, None)

    mock_extractor.assert_not_called()
    assert outcome.called is False
    assert warnings == [_RESIDUAL.format("Привет")]


@pytest.mark.asyncio
@patch("app.ai.enrichment.call_free_text_extractor")
async def test_greeting_dropped_but_other_fragments_still_call(mock_extractor, mock_settings):
    """Есть другой остаток → вызов происходит, но приветствия в fragments нет."""
    mock_extractor.return_value = FreeTextCriteriaAnswer(
        sort=Sort.PRICE_ASC,
        consumed_fragments=["по возрастанию стоимости"],
    )
    criteria = Criteria()
    warnings = [
        _RESIDUAL.format("Добрый день"),
        _RESIDUAL.format("по возрастанию стоимости"),
    ]

    outcome = await resolve_free_text_criteria(
        criteria, "Добрый день, квартира по возрастанию стоимости", warnings, None
    )

    assert outcome.called is True
    context = mock_extractor.call_args.args[1]
    assert context["unresolved_fragments"] == ["по возрастанию стоимости"]
    # Приветствие модели не показывали — и его warning остался на месте
    # (снят только тот фрагмент, который модель реально разобрала).
    assert warnings == [_RESIDUAL.format("Добрый день")]


def test_greeting_forms_recognised():
    """Набор форм: падежей нет, но регистр/пунктуация/ё встречаются."""
    greetings = [
        "привет",
        "Привет",
        "Приветствую",
        "здравствуйте",
        "Здрасьте",
        "Доброе утро",
        "добрый день",
        "Доброго времени суток",
        "добрый вечер",
        "хай",
    ]
    assert _residual_fragments([_RESIDUAL.format(g) for g in greetings]) == []
    # Приветствие внутри содержательного фрагмента остатком быть не перестаёт.
    assert _residual_fragments([_RESIDUAL.format("привет хочу вот такую")]) == [
        "привет хочу вот такую"
    ]
