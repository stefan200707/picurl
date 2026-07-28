"""Аудит ВЫДАЧИ: сверяет сгенерированную ссылку с содержимым карточек pik.ru.

Чем отличается от остальных контуров проекта.

``scripts/parse_audit.py`` и ``scripts/complex_audit.py`` останавливаются на URL:
они сравнивают запрос пользователя со ссылкой и никогда — ссылку с выдачей.
``app.pik.validator`` идёт на шаг дальше, но смотрит только на ``count``, а
``count`` подтверждает согласованность параметра с самим собой при ЛЮБОЙ его
трактовке: если бы ``floorFrom=7`` означало «ровно седьмой этаж», счётчик был бы
так же непротиворечив.

Этот скрипт закрывает ровно тот пробел. Он берёт ссылку, которую построил наш
``build_url``, дёргает настоящий бэкенд сайта и достаёт из ответа ФАКТИЧЕСКИЕ
значения полей по всем полученным карточкам: этаж, цену, площадь, комнатность,
дату сдачи. Затем проверяет, что каждое значение попадает в интервал, который
запрошен в самой ссылке. Нарушение печатается с примером конкретной карточки —
«не сошлось» без предъявления карточки здесь бесполезно.

Прецедент, ради которого контур написан: тревога по ``floorFrom`` (2026-07-28)
была закрыта списком этажей ``7, 10, 11, 13, 16, 20, 21`` в выдаче — никакой
счётчик закрыть её не мог. Тогда список добывался руками в браузере; здесь то же
самое делается программой.

Три вещи, которые сделаны намеренно и которые нельзя «улучшить» не подумав.

1. **Ожидание берётся из URL, а не из ``Criteria``.** Проверяется именно ссылка:
   что она означает для бэкенда, а не что мы имели в виду. Сверка с намерением —
   задача других контуров.
2. **В запрос уходят ровно параметры нашей ссылки** плюс cache-buster. Никаких
   «полезных» добавок вроде ``location=2,3``: добавка изменила бы выдачу, и
   проверялась бы уже не наша ссылка. Следствие честное и его надо знать: ссылка
   без локационного фильтра захватит и регионы.
3. **Непереводимый фрагмент ссылки — это отказ, а не пропуск.** Неизвестный
   сегмент пути или неизвестный query-параметр останавливают проверку случая с
   явным сообщением. Молча выкинуть непонятое здесь означало бы проверять не ту
   ссылку.
4. **Основная проверка односторонняя, и это признано, а не забыто.** «Все
   значения внутри интервала» ловит границу, понятую ШИРЕ запрошенной, и слепа к
   границе, понятой УЖЕ: выдача из одних семёрок вложена в «7 и выше». Поэтому
   отдельно считается признак «все значения совпали с самой границей» (вердикт
   ``ПОДОЗРЕНИЕ``), и отчёт всегда печатает список фактических значений — тревога
   2026-07-28 была снята именно чтением списка, а не агрегатом.

Чего контур не проверяет по построению: сверку ссылки с НАМЕРЕНИЕМ пользователя.
Выдуманный фильтр (класс «три сфабрикованных ``timeOnFoot``», AI-25) бэкенд
применит честно, и здесь всё сойдётся. Виден будет только его побочный эффект —
пустая выдача.

Почему НЕ в ``pytest``: данные живые. Остаток продаж меняется день ото дня
(``count`` того же ЖК за один прогон уехал 55 → 54), эндпоинт внутренний и
недокументированный, а флапающий тест хуже отсутствующего. Скрипт лежит в
``scripts/`` — ``testpaths = ["tests"]`` его не собирает. Инвариант 6 не задет:
в сеть ходит скрипт, рантайм по-прежнему читает справочники только с диска.

Запуск:
    uv run python scripts/outcome_audit.py
    uv run python scripts/outcome_audit.py --case 1 --max-pages 10
    uv run python scripts/outcome_audit.py --quiet --json-out /tmp/outcome.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Эндпоинт списка КВАРТИР. Соседний `block-with-flats` (см. `scripts/block_with_flats_poc.py`)
# отдаёт ЖК с превью из 12 карточек и не умеет листаться: `flatLimit`/`flatPage` на нём
# без эффекта (замерено). Здесь нужны все карточки, поэтому взят `/filter/flat`:
# 20 карточек на страницу, `flatPage` работает, `stats.lastPage` говорит сколько страниц.
ENDPOINT = "https://flat.pik-service.ru/api/v1/filter/flat"

# UA обязателен и обязан быть браузерным. При любом небраузерном UA эндпоинт отдаёт
# HTTP 200 и валидный JSON с НЕПРИМЕНЁННЫМИ фильтрами (24088 против 55) — тихая заглушка,
# самый опасный вид ответа для такого контура. Замер 2026-07-28 объяснял отсев подстрокой
# `curl`, но перемер показал шире: `picurl/1.0` и `python-httpx/0.27.0` заглушку тоже
# получают. Не заменять на честный UA проекта — это не вежливость, а порча замера.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Признак настоящего (не заглушечного) ответа: ключ `statsEmpty` в `data.time`.
# Отличать по `count` нельзя — «фильтр не понят» и «фильтр дал baseline» неразличимы.
_REAL_RESPONSE_MARKER = "statsEmpty"

FLATS_PER_PAGE = 20

# Сегменты пути pik.ru/search → параметры бэкенда.
_PATH_SEGMENT_PARAMS: dict[str, tuple[str, str]] = {
    "studio": ("rooms", "-1"),
    "one-room": ("rooms", "1"),
    "two-room": ("rooms", "2"),
    "three-room": ("rooms", "3"),
    "finish": ("hasFinish", "1"),
    "bez-otdelki": ("hasFinish", "0"),
    "predchistovaya-otdelka": ("hasFinish", "2"),
    "ready": ("ready", "1"),
}

# Query-параметры ссылки, которые бэкенд принимает под тем же именем.
_PASSTHROUGH_QUERY_PARAMS = frozenset(
    {
        "rooms",
        "blocks",
        "metroStations",
        "districtLocations",
        "districtCounties",
        "hasFinish",
        "ready",
        "status",
        "priceFrom",
        "priceTo",
        "areaFrom",
        "areaTo",
        "areaKitchenFrom",
        "areaKitchenTo",
        "floorFrom",
        "floorTo",
        "notFirstFloor",
        "lastFloor",
        "notLastFloor",
        "timeOnFoot",
        "timeOnTransport",
        "settlementYearFrom",
        "settlementYearTo",
        "settlementMonthFrom",
        "settlementMonthTo",
        "currentBenefit",
        "optionGroups",
        "options",
        "requiredTags",
        "sortBy",
        "orderBy",
    }
)

# Переименования: имя в ссылке pik.ru ≠ имя в этом бэкенде. Список закрытый и
# обоснованный замером; догадки сюда не добавлять.
_RENAMED_QUERY_PARAMS: dict[str, str] = {"type": "types"}

# Параметры, про которые ЗАМЕРЕНО, что бэкенд их принимает и игнорирует.
# Ось, целиком состоящая из таких параметров, не может дать вердикт «нарушение»:
# нарушают не мы, фильтра просто нет. Держать список явным, а не выводить из
# результата, — иначе контур будет объяснять любой провал «наверное, игнорируется».
_BACKEND_IGNORES = frozenset(
    {
        "settlementYearFrom",
        "settlementYearTo",
        "settlementMonthFrom",
        "settlementMonthTo",
    }
)


class TranslationError(RuntimeError):
    """Ссылку не удалось перевести в запрос к бэкенду без домыслов."""


class StubResponseError(RuntimeError):
    """Бэкенд отдал тихую заглушку с неприменёнными фильтрами."""


def _get_floor(flat: dict[str, Any]) -> int | None:
    return flat.get("floor")


def _get_price(flat: dict[str, Any]) -> int | None:
    return flat.get("price")


def _get_area(flat: dict[str, Any]) -> float | None:
    return flat.get("area")


def _get_rooms(flat: dict[str, Any]) -> int | None:
    return flat.get("rooms")


def _get_settlement_year(flat: dict[str, Any]) -> int | None:
    """Год из `settlementDate`. Отдельного поля с годом в карточке нет."""
    raw = flat.get("settlementDate")
    if not raw:
        return None
    try:
        return int(str(raw)[:4])
    except ValueError:
        return None


@dataclass(frozen=True)
class Axis:
    """Ось проверки: параметр(ы) ссылки ↔ поле карточки."""

    name: str
    card_field: str
    getter: Callable[[dict[str, Any]], Any]
    param_from: str | None = None
    param_to: str | None = None
    param_set: str | None = None
    unit: str = ""


AXES: tuple[Axis, ...] = (
    Axis("этаж", "floor", _get_floor, param_from="floorFrom", param_to="floorTo"),
    Axis("цена", "price", _get_price, param_from="priceFrom", param_to="priceTo", unit="₽"),
    Axis("площадь", "area", _get_area, param_from="areaFrom", param_to="areaTo", unit="м²"),
    Axis(
        "год сдачи",
        "settlementDate",
        _get_settlement_year,
        param_from="settlementYearFrom",
        param_to="settlementYearTo",
    ),
    Axis("комнатность", "rooms", _get_rooms, param_set="rooms"),
)


@dataclass
class ReferenceCase:
    """Эталонный запрос: текст и ось, ради которой он в наборе."""

    text: str
    targets: str


# Эталонный набор — единицы, не десятки. Каждый случай задаёт интервальный или
# перечислимый фильтр, значение которого видно в карточке. Полигон почти везде один
# ЖК («Руставели 14», blocks=477): узкая выдача укладывается в несколько страниц,
# то есть проверяются ВСЕ карточки, а не выборка.
REFERENCE_CASES: tuple[ReferenceCase, ...] = (
    ReferenceCase("однокомнатную квартиру в Руставели 14 от 7 этажа", "этаж"),
    ReferenceCase("однушку в ЖК Руставели 14 с 7 по 12 этаж", "этаж"),
    ReferenceCase("однокомнатную в Руставели 14 не выше 12 этажа", "этаж"),
    ReferenceCase("квартиру в Руставели 14 сдача до 2027 года", "год сдачи"),
    ReferenceCase("квартиру в ЖК Руставели 14 не дороже 21 млн", "цена"),
    ReferenceCase("квартиру в Руставели 14 площадью от 40 до 60 метров", "площадь"),
    ReferenceCase("двушку или трёшку в Руставели 14", "комнатность"),
)


@dataclass
class AxisVerdict:
    """Результат по одной оси одного случая."""

    axis: str
    requested: str
    verdict: str = ""
    checked: int = 0
    actual_min: Any = None
    actual_max: Any = None
    actual_values: list[Any] = field(default_factory=list)
    violations: int = 0
    example: str | None = None
    note: str | None = None


@dataclass
class CaseResult:
    text: str
    targets: str
    url: str = ""
    warnings: list[str] = field(default_factory=list)
    endpoint_params: dict[str, str] = field(default_factory=dict)
    backend_count: int | None = None
    flats_fetched: int = 0
    pages_fetched: int = 0
    truncated: bool = False
    axes: list[AxisVerdict] = field(default_factory=list)
    error: str | None = None


@dataclass
class OutcomeReport:
    endpoint: str
    total_cases: int
    num_errors: int
    num_violations: int
    num_unenforced: int
    num_suspicions: int
    results: list[CaseResult]


def _disable_ai() -> None:
    """Аудит обязан быть детерминированным: ИИ выключается до импорта прикладного кода."""
    from app.config import get_settings

    get_settings.cache_clear()
    get_settings().AI_ENRICHMENT_ENABLED = False


def url_to_endpoint_params(url: str) -> dict[str, str]:
    """Переводит ссылку pik.ru/search в query для бэкенда.

    Любой неопознанный сегмент пути или query-параметр — :class:`TranslationError`.
    Тихо выкинуть непонятое нельзя: проверялась бы не та ссылка.
    """
    parts = urlsplit(url)
    if parts.netloc != "www.pik.ru":
        raise TranslationError(f"чужой хост: {parts.netloc!r}")

    segments = [s for s in parts.path.strip("/").split("/") if s]
    if not segments or segments[0] != "search":
        raise TranslationError(f"путь не /search: {parts.path!r}")

    params: dict[str, str] = {}
    for segment in segments[1:]:
        mapped = _PATH_SEGMENT_PARAMS.get(segment)
        if mapped is None:
            raise TranslationError(
                f"неизвестный сегмент пути {segment!r} — перевод в запрос к бэкенду "
                "потребовал бы догадки"
            )
        key, value = mapped
        params[key] = value

    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in _RENAMED_QUERY_PARAMS:
            params[_RENAMED_QUERY_PARAMS[key]] = value
        elif key in _PASSTHROUGH_QUERY_PARAMS:
            params[key] = value
        else:
            raise TranslationError(
                f"неизвестный query-параметр {key!r} — не подтверждён замером на этом бэкенде"
            )

    return params


def fetch_flats(
    client: httpx.Client, params: dict[str, str], max_pages: int
) -> tuple[list[dict[str, Any]], int | None, int, bool]:
    """Забирает карточки постранично.

    Возвращает ``(карточки, count, страниц, усечено)``. Карточки дедуплицируются по
    ``id``: живая выдача переупорядочивается между запросами, и один и тот же лот
    успевает попасть на две страницы.
    """
    collected: dict[Any, dict[str, Any]] = {}
    count: int | None = None
    last_page = 1
    pages_fetched = 0

    page = 1
    while page <= last_page and page <= max_pages:
        request_params = dict(params)
        request_params["flatPage"] = str(page)
        request_params["_"] = str(random.getrandbits(48))

        response = client.get(ENDPOINT, params=request_params, headers={"User-Agent": BROWSER_UA})
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success", True):
            raise RuntimeError(f"бэкенд вернул success=false: {str(payload.get('data'))[:200]}")

        data = payload.get("data") or {}
        if _REAL_RESPONSE_MARKER not in (data.get("time") or {}):
            raise StubResponseError(
                "ответ без маркера `statsEmpty` — тихая заглушка с неприменёнными фильтрами"
            )

        stats = data.get("stats") or {}
        count = stats.get("count")
        last_page = int(stats.get("lastPage") or 1)
        for flat in data.get("items") or []:
            collected[flat.get("id")] = flat

        pages_fetched += 1
        page += 1
        if page <= last_page and page <= max_pages:
            time.sleep(0.3)

    truncated = last_page > max_pages
    return list(collected.values()), count, pages_fetched, truncated


def _format_value(value: Any, unit: str) -> str:
    if isinstance(value, int) and unit == "₽":
        return f"{value:,}".replace(",", " ") + " ₽"
    if unit:
        return f"{value} {unit}"
    return str(value)


def _describe_flat(flat: dict[str, Any]) -> str:
    price = flat.get("price")
    price_text = f"{price:,}".replace(",", " ") if isinstance(price, int) else str(price)
    return (
        f"кв. {flat.get('id')} ({flat.get('blockName')}, корп. {flat.get('bulkName')}): "
        f"{flat.get('rooms')}-комн., {flat.get('area')} м², "
        f"этаж {flat.get('floor')}/{flat.get('maxFloor')}, {price_text} ₽, "
        f"сдача {str(flat.get('settlementDate'))[:10]}"
    )


def _allowed_rooms(raw: str) -> set[int]:
    """Множество допустимых значений `rooms`.

    Идентификатор ``3`` в схеме проекта означает «три и более» (``Rooms.THREE_PLUS``),
    поэтому он раскрывается в открытое вверх множество, а не в точное значение.
    """
    allowed: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        value = int(chunk)
        if value == 3:
            allowed.update(range(3, 30))
        else:
            allowed.add(value)
    return allowed


def _check_axis(
    axis: Axis, params: dict[str, str], flats: list[dict[str, Any]]
) -> AxisVerdict | None:
    """Проверяет одну ось. ``None`` — ось в этой ссылке не задействована."""
    used = [p for p in (axis.param_from, axis.param_to, axis.param_set) if p and p in params]
    if not used:
        return None

    if axis.param_set:
        requested_raw = params[axis.param_set]
        allowed = _allowed_rooms(requested_raw)
        requested = f"{axis.param_set}={requested_raw}"
        lower = upper = None
    else:
        allowed = set()
        lower = float(params[axis.param_from]) if axis.param_from in params else None
        upper = float(params[axis.param_to]) if axis.param_to in params else None
        requested = " & ".join(f"{p}={params[p]}" for p in used)

    values: list[Any] = []
    missing_field = 0
    violations: list[dict[str, Any]] = []
    for flat in flats:
        value = axis.getter(flat)
        if value is None:
            missing_field += 1
            continue
        values.append(value)
        if axis.param_set:
            if int(value) not in allowed:
                violations.append(flat)
        elif (lower is not None and float(value) < lower) or (
            upper is not None and float(value) > upper
        ):
            violations.append(flat)

    verdict = AxisVerdict(axis=axis.name, requested=requested, checked=len(values))

    if not flats:
        verdict.verdict = "НЕЧЕГО ПРОВЕРЯТЬ"
        verdict.note = "бэкенд не вернул ни одной карточки"
        return verdict

    if not values:
        verdict.verdict = "НЕЛЬЗЯ ПРОВЕРИТЬ"
        verdict.note = (
            f"поля {axis.card_field!r} нет ни в одной из {len(flats)} карточек — "
            "фильтр этой оси проверить нечем"
        )
        return verdict

    verdict.actual_min = min(values)
    verdict.actual_max = max(values)
    verdict.actual_values = sorted(set(values))[:40]
    verdict.violations = len(violations)

    # Примечания копятся, а не затирают друг друга: «часть карточек без поля» — это
    # ограничение проверки, и оно обязано доехать до отчёта вместе с вердиктом.
    notes: list[str] = []
    if missing_field:
        notes.append(f"у {missing_field} карточек поле {axis.card_field!r} отсутствует")

    ignored_axis = all(p in _BACKEND_IGNORES for p in used)
    if not violations:
        verdict.verdict = "ОК"
        # Проверка «выдача ⊆ запрошенный интервал» ОДНОСТОРОННЯЯ: она ловит границу,
        # понятую шире запрошенной, и слепа к границе, понятой УЖЕ. Ровно этой формой
        # была тревога 2026-07-28 («floorFrom=7 показывает ровно седьмой этаж»):
        # выдача из одних семёрок вложена в «7 и выше» и нарушением не является.
        # Встречный признак дешёвый и точный — одиночная открытая граница, у которой
        # все фактические значения совпали с самой границей.
        open_bound = (lower is None) != (upper is None)
        bound = lower if lower is not None else upper
        if (
            not axis.param_set
            and open_bound
            and len(values) > 1
            and float(verdict.actual_min) == float(verdict.actual_max) == float(bound)
        ):
            verdict.verdict = "ПОДОЗРЕНИЕ"
            notes.append(
                f"все значения ({len(values)} шт.) равны самой границе — открытый "
                "интервал мог быть понят бэкендом как точное значение; "
                "проверить встречным запросом"
            )
        if ignored_axis:
            notes.append(
                "внимание: параметр числится игнорируемым бэкендом, а нарушений нет — "
                "совпадение или бэкенд изменился, требуется перемер"
            )
    else:
        verdict.example = _describe_flat(violations[0])
        if ignored_axis:
            verdict.verdict = "ФИЛЬТР НЕ ПРИМЕНЁН БЭКЕНДОМ"
            notes.append(
                "параметр замерен как игнорируемый: бэкенд принимает его и не фильтрует. "
                "Это не дефект генератора ссылки, но ссылка обещает пользователю больше, "
                "чем сайт выполняет"
            )
        else:
            verdict.verdict = "НАРУШЕНИЕ"

    verdict.note = "; ".join(notes) or None
    return verdict


def run_case(case: ReferenceCase, client: httpx.Client, max_pages: int) -> CaseResult:
    from app.parsing.parser import parse
    from app.pik.url_builder import build_url

    result = CaseResult(text=case.text, targets=case.targets)
    try:
        parsed = parse(case.text)
        warnings: list[str] = list(parsed.warnings)
        result.url = build_url(parsed.criteria, warnings)
        result.warnings = [str(w) for w in warnings]

        params = url_to_endpoint_params(result.url)
        result.endpoint_params = params

        flats, count, pages, truncated = fetch_flats(client, params, max_pages)
        result.backend_count = count
        result.flats_fetched = len(flats)
        result.pages_fetched = pages
        result.truncated = truncated

        for axis in AXES:
            verdict = _check_axis(axis, params, flats)
            if verdict is not None:
                result.axes.append(verdict)

        if not result.axes:
            result.error = (
                f"ни одна проверяемая ось не доехала до ссылки (ожидалась «{case.targets}») — "
                "проверять в выдаче нечего"
            )
    except Exception as exc:  # контур обязан пережить любой случай набора
        result.error = traceback.format_exception_only(type(exc), exc)[-1].strip()

    return result


def run_reference_set(
    cases: tuple[ReferenceCase, ...] = REFERENCE_CASES,
    max_pages: int = 10,
    timeout: float = 25.0,
) -> OutcomeReport:
    """Точка входа и для скрипта, и для ручного вызова из REPL."""
    _disable_ai()

    results: list[CaseResult] = []
    with httpx.Client(timeout=timeout) as client:
        for case in cases:
            results.append(run_case(case, client, max_pages))

    def _count(verdict: str) -> int:
        return sum(1 for r in results for a in r.axes if a.verdict == verdict)

    return OutcomeReport(
        endpoint=ENDPOINT,
        total_cases=len(results),
        num_errors=sum(1 for r in results if r.error),
        num_violations=_count("НАРУШЕНИЕ"),
        num_unenforced=_count("ФИЛЬТР НЕ ПРИМЕНЁН БЭКЕНДОМ"),
        num_suspicions=_count("ПОДОЗРЕНИЕ"),
        results=results,
    )


def _print_report(report: OutcomeReport) -> None:
    print("=" * 100)
    print("АУДИТ ВЫДАЧИ: ссылка ↔ фактические значения в карточках")
    print(f"Эндпоинт: {report.endpoint}")
    print("=" * 100)

    for i, res in enumerate(report.results, start=1):
        print(f"\n[{i:02d}] ЗАПРОС:   {res.text}")
        print(f"     ОСЬ:      {res.targets}")
        print(f"     ССЫЛКА:   {res.url or '—'}")
        if res.warnings:
            for w in res.warnings:
                print(f"     WARNING:  {w}")
        if res.error:
            print(f"     ОШИБКА:   {res.error}")
            continue

        query = " ".join(f"{k}={v}" for k, v in sorted(res.endpoint_params.items()))
        print(f"     ЗАПРОС К БЭКЕНДУ: {query}")
        coverage = f"{res.flats_fetched} карточек"
        if res.backend_count is not None:
            coverage += f" из count={res.backend_count}"
        coverage += f" (страниц: {res.pages_fetched})"
        if res.truncated:
            coverage += "  ⚠ УСЕЧЕНО лимитом --max-pages, выборка неполная"
        elif res.backend_count is not None and res.flats_fetched < res.backend_count:
            coverage += "  ⚠ получено меньше count — живая выдача сдвинулась между страницами"
        print(f"     ВЫБОРКА:  {coverage}")

        for axis in res.axes:
            print(f"     ── ось «{axis.axis}» ({axis.requested})")
            if axis.actual_min is not None or axis.actual_max is not None:
                print(
                    f"        фактически: {axis.actual_min} … {axis.actual_max}  "
                    f"(проверено значений: {axis.checked})"
                )
                print(f"        значения:   {axis.actual_values}")
            print(f"        ВЕРДИКТ:    {axis.verdict}")
            if axis.violations:
                print(f"        нарушений:  {axis.violations} из {axis.checked}")
                print(f"        пример:     {axis.example}")
            if axis.note:
                print(f"        примечание: {axis.note}")

    print("\n" + "=" * 100)
    print(
        f"ИТОГО: случаев {report.total_cases}, ошибок {report.num_errors}, "
        f"нарушений {report.num_violations}, подозрений {report.num_suspicions}, "
        f"фильтров не применено бэкендом {report.num_unenforced}"
    )
    print("=" * 100)


def _write_json(report: OutcomeReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), "utf-8")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", type=int, default=None, help="прогнать только случай с этим номером (с 1)"
    )
    parser.add_argument(
        "--max-pages", type=int, default=10, help="потолок страниц на случай (20 карточек/стр.)"
    )
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    cases = REFERENCE_CASES
    if args.case is not None:
        if not 1 <= args.case <= len(REFERENCE_CASES):
            print(f"нет случая {args.case}: в наборе {len(REFERENCE_CASES)}", file=sys.stderr)
            return 2
        cases = (REFERENCE_CASES[args.case - 1],)

    report = run_reference_set(cases, max_pages=args.max_pages, timeout=args.timeout)

    if args.json_out is not None:
        _write_json(report, args.json_out)
    if not args.quiet:
        _print_report(report)

    # Подозрение поднимает код возврата наравне с нарушением: контур ручной, ненулевой
    # код здесь означает «посмотри глазами», а не «сломан билд». Пропустить встречную
    # трактовку границы было бы дороже ложной тревоги.
    return 1 if (report.num_errors or report.num_violations or report.num_suspicions) else 0


if __name__ == "__main__":
    raise SystemExit(main())
