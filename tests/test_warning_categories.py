"""Г6, порция 1: носитель категории + различитель остатка в parser.

Проверяем ровно две вещи и границу между ними:
1. Каркас ничего не ломает — ``TaggedWarning`` неотличим от ``str`` для всех
   операций, на которых держится пайплайн (равенство, ``in``, ``remove``,
   регекс, pydantic, json, deepcopy). Это и есть цена выбора «дополнить, не
   ломать»: если хоть одна из них разъедется, устаревшие warning'и перестанут
   сниматься молча.
2. Различитель `parser._classify_residual` разводит исходный живой инцидент:
   «Привет» → noise, «этаж от 7» → lost.
"""

import copy
import json
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.api.endpoints import get_http_client
from app.api.schemas import BuildUrlResponse, WarningDetail
from app.main import app
from app.parsing.parser import _classify_residual, parse
from app.warnings import (
    TaggedWarning,
    WarningCategory,
    WarningSeverity,
    category_of,
    describe,
    severity_of,
)

_UNRECOGNIZED_SUFFIX = "»: не удалось распознать, не попало в ссылку"


# --------------------------------------------------------------------------- #
# 1. Каркас: подкласс str остаётся строкой для всего пайплайна
# --------------------------------------------------------------------------- #


def test_tagged_warning_is_a_plain_string_everywhere():
    text = "«этаж от 7»: не удалось распознать, не попало в ссылку"
    w = TaggedWarning(text, WarningCategory.LOST)

    assert w == text and text == w
    assert isinstance(w, str)
    assert hash(w) == hash(text)
    assert w.startswith("«") and w.endswith(_UNRECOGNIZED_SUFFIX)
    assert re.match(r"«(.+?)»: не удалось распознать", w).group(1) == "этаж от 7"

    # Механика снятия устаревших warning'ов (app.ai.enrichment) — по точному
    # равенству строк. Именно здесь ошибка была бы молчаливой.
    channel = [w]
    assert text in channel
    channel.remove(text)
    assert channel == []


def test_tagged_warning_survives_json_and_deepcopy():
    """`scripts/*_audit.py` гоняют warnings через `asdict` (= deepcopy) и json."""
    w = TaggedWarning("«привет" + _UNRECOGNIZED_SUFFIX, WarningCategory.NOISE)

    assert json.dumps([w], ensure_ascii=False) == json.dumps([str(w)], ensure_ascii=False)

    clone = copy.deepcopy(w)
    assert clone == w
    assert clone.category is WarningCategory.NOISE


def test_unmarked_string_is_unknown_not_an_error():
    """Неразмеченная точка (сегодня их большинство) — нормальный `unknown`."""
    assert category_of("под критерии ничего не найдено") is WarningCategory.UNKNOWN
    assert TaggedWarning("без категории").category is WarningCategory.UNKNOWN
    assert describe("что угодно") == ("что угодно", WarningCategory.UNKNOWN, WarningSeverity.ERROR)


def test_severity_is_derived_from_category():
    assert severity_of(WarningCategory.LOST) is WarningSeverity.ERROR
    assert severity_of(WarningCategory.UNKNOWN) is WarningSeverity.ERROR
    assert severity_of(WarningCategory.DEGRADED) is WarningSeverity.WARNING
    assert severity_of(WarningCategory.CAPPED) is WarningSeverity.WARNING
    assert severity_of(WarningCategory.UNVERIFIED) is WarningSeverity.INFO
    assert severity_of(WarningCategory.NOISE) is WarningSeverity.INFO
    assert severity_of(WarningCategory.INFO) is WarningSeverity.INFO
    assert TaggedWarning("x", WarningCategory.LOST).severity is WarningSeverity.ERROR


def test_new_string_loses_the_category_documented_limitation():
    """Зафиксированное ограничение формы: новая строка категорию не наследует."""
    w = TaggedWarning("текст", WarningCategory.LOST)
    assert category_of(w + " хвост") is WarningCategory.UNKNOWN
    assert category_of(w.strip()) is WarningCategory.UNKNOWN
    assert category_of(f"{w}") is WarningCategory.UNKNOWN


def test_pydantic_coerces_to_str_so_details_are_built_before_the_model():
    """Причина, по которой warnings_detailed собирается ДО BuildUrlResponse."""

    class M(BaseModel):
        warnings: list[str] = []

    w = TaggedWarning("текст", WarningCategory.LOST)
    assert category_of(M(warnings=[w]).warnings[0]) is WarningCategory.UNKNOWN


# --------------------------------------------------------------------------- #
# 2. Различитель остатка: обе стороны границы
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "fragment",
    [
        "этаж от 7",  # исходный инцидент: слово-параметр
        "этаж выше третьего",
        "окна во двор",  # реальный фильтр vidVoDvor, не доехавший до ссылки
        "санузлов несколько",
        "отделка готовая",
        "заселение не позже 2027 года",
        "не дальше 2 км",  # число + единица
        "не дальше 3 километров",
        "до 12 минут",
        "до 15 млн",
    ],
)
def test_residual_with_a_filter_word_or_number_is_lost(fragment):
    assert _classify_residual(fragment) is WarningCategory.LOST


@pytest.mark.parametrize(
    "fragment",
    ["Привет", "привет", "здравствуйте", "спасибо", "помогите пожалуйста", "добрый день"],
)
def test_residual_of_greetings_only_is_noise(fragment):
    assert _classify_residual(fragment) is WarningCategory.NOISE


@pytest.mark.parametrize(
    "fragment",
    [
        "с видом на закат",  # ни параметра, ни числа с единицей — честно спорно
        "хорошая инфраструктура",
        "не знаю что хочу",
        "что посоветуете",
        "хачу квартеру",
        "не ниже пятого",  # число прописью без единицы — в спорное, не в lost
    ],
)
def test_everything_disputable_stays_unknown(fragment):
    """Правило асимметрично: `noise` на реальной потере хуже плоского списка,
    поэтому спорное остаётся видимым (`unknown` → severity error)."""
    assert _classify_residual(fragment) is WarningCategory.UNKNOWN


def test_live_incident_is_split_by_category():
    """Исходный повод задачи: две строки, неотличимые в плоском list[str].

    Тест держится за живое поведение парсера: «этаж от N» сегодня не матчится ни
    одним правилом этажа (все требуют «этаж» ПОСЛЕ числа). Когда инверсию
    научатся распознавать, warning исчезнет и тест упадёт — это ожидаемо и
    означает «остаток закрыт», а не поломку разметки: инвариант границы держит
    `test_residual_*` выше, они от правил парсинга не зависят.
    """
    result = parse("Привет, ищу двушку до 15 млн, этаж от 7")

    by_category = {category_of(w): str(w) for w in result.warnings}
    assert by_category[WarningCategory.NOISE].startswith("«Привет»")
    assert by_category[WarningCategory.LOST].startswith("«этаж от 7»")

    # Плоские тексты не изменились ни на байт.
    assert [str(w) for w in result.warnings] == [
        "«Привет»: не удалось распознать, не попало в ссылку",
        "«этаж от 7»: не удалось распознать, не попало в ссылку",
    ]


# --------------------------------------------------------------------------- #
# 3. API: warnings выводится из warnings_detailed
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(monkeypatch):
    # ИИ выключаем явно: остаток «этаж от 7» дотягивается до free-text-экстрактора
    # и звал бы живую модель. Здесь проверяется канал warnings, не ИИ.
    from app.config import get_settings

    monkeypatch.setenv("AI_ENRICHMENT_ENABLED", "false")
    settings = get_settings()
    monkeypatch.setattr(settings, "AI_ENRICHMENT_ENABLED", False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 47})

    mock = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_http_client] = lambda: mock
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_build_url_response_carries_detailed_warnings(client):
    response = client.post("/build-url", json={"text": "Привет, ищу двушку до 15 млн, этаж от 7"})
    assert response.status_code == 200
    data = response.json()

    # Плоский список выводится из detailed: те же тексты, тот же порядок.
    assert data["warnings"] == [d["text"] for d in data["warnings_detailed"]]

    detailed = {d["text"]: d for d in data["warnings_detailed"]}
    incident_noise = "«Привет" + _UNRECOGNIZED_SUFFIX
    incident_lost = "«этаж от 7" + _UNRECOGNIZED_SUFFIX
    assert detailed[incident_noise]["category"] == "noise"
    assert detailed[incident_noise]["severity"] == "info"
    assert detailed[incident_lost]["category"] == "lost"
    assert detailed[incident_lost]["severity"] == "error"


def test_unmarked_points_are_reported_as_unknown_not_omitted(client):
    """Неразмеченные точки (порции 3-5) обязаны доехать до detailed как unknown."""
    response = client.post("/build-url", json={"text": "хочу вторичку в кирпичном доме"})
    data = response.json()

    assert len(data["warnings_detailed"]) == len(data["warnings"])
    capped_yet_unmarked = [
        d for d in data["warnings_detailed"] if "не поддерживается pik.ru" in d["text"]
    ]
    assert capped_yet_unmarked
    assert all(d["category"] == "unknown" for d in capped_yet_unmarked)


def test_response_model_defaults_keep_both_channels_empty():
    empty = BuildUrlResponse(url="https://www.pik.ru/search")
    assert empty.warnings == []
    assert empty.warnings_detailed == []
    assert WarningDetail.from_warning(
        TaggedWarning("текст", WarningCategory.DEGRADED)
    ) == WarningDetail(
        text="текст", category=WarningCategory.DEGRADED, severity=WarningSeverity.WARNING
    )
