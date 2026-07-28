"""Аудит справочных id против НАСТОЯЩЕГО эндпоинта выдачи pik.ru.

Офлайн-инструмент уровня ``refresh*``: ходит в сеть, но **не является частью
рантайма** и не нарушает инвариант 6 — рантайм по-прежнему читает JSON с диска,
а проверочный запрос шлёт в ``api.pik.ru/v2/filter``. Здесь другой бэкенд::

    GET https://flat.pik-service.ru/api/v1/filter/block-with-flats

Зачем. ``api.pik.ru/v2/filter`` (куда ходит наш валидатор) МОЛЧА ИГНОРИРУЕТ
``metroStations``/``districtLocations``/``districtCounties`` — через него
локационные id непроверяемы в принципе. Настоящий эндпоинт их применяет, значит
им можно проверить, живы ли наши id. Рантайм на него НЕ переводится: ловушка с
User-Agent описана двумя замерами по-разному и не понята до конца, а
деградирует она молча (HTTP 200 + валидный JSON + неверные числа).

Что значит вердикт
------------------

Проверка — **сужением выдачи**, а не наличием значения в фасете: фасет
перечисляет доступное ПРИ ТЕКУЩЕМ ФИЛЬТРЕ, поэтому отсутствие в нём id не
доказывает, что id невалиден (на этой ошибке трижды спотыкался шаг K).
Членство в фасете печатается как справка, в вердикт не входит.

* ``живой``      — фильтр применён и сузил выдачу: ``0 < count < baseline``.
* ``мёртвый``    — id бэкенд признал, но предложений нет: ``count == 0``.
* ``невалидный`` — id бэкенду неизвестен. Как именно это видно — **зависит от
  параметра**, и в этом главная ловушка:

  =================== ========================= ==============================
  параметр            мусорное значение         как отличить невалидный
  =================== ========================= ==============================
  ``metroStations``   ``deadbeef-…``            молча = baseline (НЕ ошибка!)
  ``districtLocations`` ``999999``              HTTP 500
  ``districtCounties``  ``999999``              HTTP 500
  ``blocks``          ``999999``                ``count=0`` — НЕОТЛИЧИМО от
                                                мёртвого
  =================== ========================= ==============================

  Поэтому у ``blocks`` вердикт честно называется ``мёртвый/невалидный`` —
  через этот эндпоинт эти два случая не различаются, и врать об этом нельзя.

Перед прогоном каждого справочника скрипт шлёт **контрольное мусорное
значение** и проверяет, что бэкенд ведёт себя так, как описано в таблице. Если
не так — вся классификация этого справочника недостоверна, и скрипт падает.

Детектор заглушки (обязателен)
------------------------------

Небраузерный User-Agent даёт HTTP 200, синтаксически валидный JSON и
НЕПРИМЕНЁННЫЕ фильтры. Отличить по числам нельзя. Единственный надёжный
признак — состав ``data.time``, и он **у каждого маршрута свой**::

    /api/v1/filter/block-with-flats  →  filter_block
    /api/v1/filter/block             →  statsEmpty
    /api/v1/filter/flat              →  statsEmpty

Поймав ответ без маркера, скрипт ПАДАЕТ (``StubResponseError``, код возврата 2)
и не печатает ни одной цифры: молча объявить живые id мёртвыми — худший из
возможных исходов, он тихо разрушил бы справочники.

Проверить сам детектор::

    uv run python scripts/reference_audit.py --stub-ua --kinds counties

Запуск::

    uv run python scripts/reference_audit.py
    uv run python scripts/reference_audit.py --kinds metro,blocks
    uv run python scripts/reference_audit.py --counties-probe
    uv run python scripts/reference_audit.py --json-out /tmp/refaudit.json
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.reference.loader import RefEntry, load_all

HOST = "https://flat.pik-service.ru"
ENDPOINT = f"{HOST}/api/v1/filter/block-with-flats"
BLOCK_REGISTRY_ENDPOINT = f"{HOST}/api/v1/filter/block"

#: UA обязан быть браузерным. Это не вежливость, а условие корректности замера:
#: `picurl/1.0` и `python-httpx/*` получают нефильтрованную заглушку.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
#: Заведомо «плохой» UA — только для --stub-ua, чтобы показать работу детектора.
STUB_UA = "curl/8.7.1"

#: Маркер настоящего (не заглушечного) ответа. Свой у каждого маршрута.
REAL_RESPONSE_MARKERS: dict[str, str] = {
    ENDPOINT: "filter_block",
    BLOCK_REGISTRY_ENDPOINT: "statsEmpty",
}

#: Москва + МО. Без него в выдачу попадают регионы и baseline «плывёт».
MOSCOW_REGION = "2,3"

VERDICT_ALIVE = "живой"
VERDICT_DEAD = "мёртвый"
VERDICT_INVALID = "невалидный"
VERDICT_DEAD_OR_INVALID = "мёртвый/невалидный"
VERDICT_NOT_NARROWED = "не сузил (аномалия)"
VERDICT_NO_ID = "нет id"


class StubResponseError(RuntimeError):
    """Бэкенд отдал тихую заглушку с неприменёнными фильтрами."""


class ControlProbeError(RuntimeError):
    """Мусорное значение повело себя не так, как требует классификация."""


class BackendRejected(RuntimeError):
    """Бэкенд отверг значение (HTTP 5xx или success=false) — id невалиден."""


@dataclass(frozen=True)
class KindSpec:
    """Как проверять один справочник."""

    key: str
    param: str
    title: str
    #: Имя атрибута в ``ReferenceData``. Совпадает с ``key`` везде, кроме ЖК:
    #: справочник называется ``complexes``, а URL-параметр — ``blocks``.
    ref_attr: str
    #: Базис запроса. У blocks НЕТ `location`: он отсекал бы региональные ЖК,
    #: и они попадали бы в «мёртвые» по нашей же вине.
    basis: dict[str, str]
    #: Заведомо несуществующее значение — контрольная проба.
    garbage: str
    #: Как бэкенд сигналит о невалидном значении: silent | error | zero.
    garbage_mode: str
    #: Путь к фасету внутри data.stats (только для справки).
    facet_path: tuple[str, ...]


BASIS_MOSCOW: dict[str, str] = {
    "types": "1,2",
    "location": MOSCOW_REGION,
    "onlyFlats": "1",
    "flatLimit": "1",
}
BASIS_ALL_REGIONS: dict[str, str] = {
    "types": "1,2",
    "onlyFlats": "1",
    "flatLimit": "1",
}

KINDS: tuple[KindSpec, ...] = (
    KindSpec(
        key="metro",
        ref_attr="metro",
        param="metroStations",
        title="метро (GUID → metroStations)",
        basis=BASIS_MOSCOW,
        garbage="deadbeef-dead-dead-dead-deaddeafbeef",
        garbage_mode="silent",
        facet_path=("district", "metroStations"),
    ),
    KindSpec(
        key="districts",
        ref_attr="districts",
        param="districtLocations",
        title="районы (id → districtLocations)",
        basis=BASIS_MOSCOW,
        garbage="999999",
        garbage_mode="error",
        facet_path=("district", "districtLocations"),
    ),
    KindSpec(
        key="counties",
        ref_attr="counties",
        param="districtCounties",
        title="округа (id → districtCounties)",
        basis=BASIS_MOSCOW,
        garbage="999999",
        garbage_mode="error",
        facet_path=("district", "districtCounties"),
    ),
    KindSpec(
        key="blocks",
        ref_attr="complexes",
        param="blocks",
        title="ЖК (id → blocks)",
        basis=BASIS_ALL_REGIONS,
        garbage="999999",
        garbage_mode="zero",
        facet_path=("blocks",),
    ),
)

KINDS_BY_KEY = {spec.key: spec for spec in KINDS}


@dataclass
class IdVerdict:
    kind: str
    name: str
    ref_id: str | None
    verdict: str
    count: int | None
    baseline: int
    in_facet: bool | None
    note: str = ""


@dataclass
class KindReport:
    kind: str
    title: str
    param: str
    baseline: int
    facet_size: int | None
    control: str
    total_entries: int
    without_id: int
    verdicts: list[IdVerdict] = field(default_factory=list)

    def tally(self) -> dict[str, int]:
        return dict(Counter(v.verdict for v in self.verdicts))


@dataclass
class CountyProbe:
    facet_id: int
    count: int
    block_ids: list[int]
    derived_names: dict[str, int]
    derived_districts: dict[str, int]
    best_name: str | None
    known_blocks: int
    block_names: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    endpoint: str
    kinds: list[KindReport] = field(default_factory=list)
    counties_probe: list[CountyProbe] = field(default_factory=list)
    counties_facet: list[int] = field(default_factory=list)


# --------------------------------------------------------------------------
# Сеть
# --------------------------------------------------------------------------


def fetch(
    client: httpx.Client,
    params: dict[str, str],
    *,
    user_agent: str = BROWSER_UA,
    endpoint: str = ENDPOINT,
) -> dict[str, Any]:
    """Один запрос. Возвращает ``payload["data"]`` после всех проверок.

    Порядок проверок принципиален: сперва транспорт, потом ``success``, потом
    маркер заглушки — и только затем можно читать числа.
    """
    request_params = dict(params)
    request_params["_"] = str(random.getrandbits(48))

    response = client.get(endpoint, params=request_params, headers={"User-Agent": user_agent})

    if response.status_code >= 500:
        raise BackendRejected(f"HTTP {response.status_code}")
    response.raise_for_status()

    payload = response.json()
    if not payload.get("success", True):
        raise BackendRejected(f"success=false: {str(payload.get('data'))[:120]}")

    data = payload.get("data") or {}
    marker = REAL_RESPONSE_MARKERS[endpoint]
    if marker not in (data.get("time") or {}):
        raise StubResponseError(
            f"ответ {endpoint} без маркера `{marker}` в data.time "
            f"(есть: {sorted((data.get('time') or {}).keys())}) — "
            "это тихая заглушка с НЕПРИМЕНЁННЫМИ фильтрами. "
            "Числам верить нельзя, аудит прерван."
        )
    return data


def fetch_count(
    client: httpx.Client,
    params: dict[str, str],
    *,
    user_agent: str = BROWSER_UA,
) -> tuple[int, dict[str, Any]]:
    data = fetch(client, params, user_agent=user_agent)
    stats = data.get("stats") or {}
    return int(stats.get("count") or 0), stats


def dig(stats: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = stats
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


# --------------------------------------------------------------------------
# Контрольная проба: убедиться, что бэкенд сигналит так, как мы думаем
# --------------------------------------------------------------------------


def run_control_probe(client: httpx.Client, spec: KindSpec, baseline: int, pause: float) -> str:
    """Послать заведомо мусорное значение и сверить реакцию с ожидаемой.

    Если бэкенд поменял поведение, вердикты этого справочника перестают
    что-либо значить — падаем, а не молчим.
    """
    params = {**spec.basis, spec.param: spec.garbage}
    time.sleep(pause)
    try:
        count, _ = fetch_count(client, params)
    except BackendRejected as exc:
        if spec.garbage_mode == "error":
            return f"мусор `{spec.garbage}` → {exc} (ожидаемо)"
        raise ControlProbeError(
            f"{spec.param}: мусор `{spec.garbage}` дал ошибку ({exc}), "
            f"а ожидался режим `{spec.garbage_mode}`. Классификация недостоверна."
        ) from exc

    if spec.garbage_mode == "silent":
        if count != baseline:
            raise ControlProbeError(
                f"{spec.param}: мусор `{spec.garbage}` дал count={count}, "
                f"а ожидался baseline={baseline} (режим `silent`). "
                "Значит, признак «невалидный == baseline» больше не работает."
            )
        return f"мусор `{spec.garbage}` → count={count} == baseline (ожидаемо, молча)"

    if spec.garbage_mode == "zero":
        if count != 0:
            raise ControlProbeError(
                f"{spec.param}: мусор `{spec.garbage}` дал count={count}, а ожидался 0."
            )
        return f"мусор `{spec.garbage}` → count=0 (ожидаемо; мёртвый неотличим)"

    raise ControlProbeError(
        f"{spec.param}: мусор `{spec.garbage}` вернул count={count} без ошибки, "
        f"а ожидался HTTP 500 (режим `error`). Классификация недостоверна."
    )


# --------------------------------------------------------------------------
# Аудит одного справочника
# --------------------------------------------------------------------------


def classify(spec: KindSpec, count: int | None, baseline: int, rejected: bool) -> tuple[str, str]:
    if rejected:
        return VERDICT_INVALID, "бэкенд отверг значение"
    assert count is not None
    if count == 0:
        if spec.garbage_mode == "zero":
            return VERDICT_DEAD_OR_INVALID, "через этот эндпоинт случаи неразличимы"
        return VERDICT_DEAD, "id признан, предложений нет"
    if count >= baseline:
        if spec.garbage_mode == "silent":
            return VERDICT_INVALID, "выдача не сузилась — id молча проигнорирован"
        return VERDICT_NOT_NARROWED, "выдача не сузилась, но и ошибки нет"
    return VERDICT_ALIVE, ""


def audit_kind(
    client: httpx.Client,
    spec: KindSpec,
    entries: tuple[RefEntry, ...],
    *,
    pause: float,
    limit: int | None,
    confirm: int,
) -> KindReport:
    baseline, base_stats = fetch_count(client, spec.basis)
    if baseline <= 0:
        raise RuntimeError(f"{spec.key}: baseline={baseline} — замер бессмыслен")

    facet = dig(base_stats, spec.facet_path)
    facet_values = {str(v) for v in facet} if isinstance(facet, list) else set()

    control = run_control_probe(client, spec, baseline, pause)

    with_id = [e for e in entries if e.id]
    without_id = len(entries) - len(with_id)
    if limit is not None:
        with_id = with_id[:limit]

    report = KindReport(
        kind=spec.key,
        title=spec.title,
        param=spec.param,
        baseline=baseline,
        facet_size=len(facet_values) if facet_values else None,
        control=control,
        total_entries=len(entries),
        without_id=without_id,
    )

    for entry in with_id:
        time.sleep(pause)
        params = {**spec.basis, spec.param: str(entry.id)}
        count: int | None = None
        rejected = False
        try:
            count, _ = fetch_count(client, params)
        except BackendRejected:
            # 5xx бывает и транзиентным — переспрашиваем, прежде чем клеймить.
            time.sleep(pause * 2)
            try:
                count, _ = fetch_count(client, params)
            except BackendRejected:
                rejected = True

        verdict, note = classify(spec, count, baseline, rejected)

        # Нули и «не сузил» подтверждаем повторами: это самые дорогие вердикты.
        if confirm and verdict in (VERDICT_DEAD, VERDICT_DEAD_OR_INVALID, VERDICT_INVALID):
            for _ in range(confirm):
                time.sleep(pause)
                try:
                    again, _ = fetch_count(client, params)
                except BackendRejected:
                    continue
                if count is not None and again != count:
                    note = f"{note}; НЕСТАБИЛЬНО: повтор дал {again}".strip("; ")
                    break

        report.verdicts.append(
            IdVerdict(
                kind=spec.key,
                name=entry.name,
                ref_id=entry.id,
                verdict=verdict,
                count=count,
                baseline=baseline,
                in_facet=(str(entry.id) in facet_values) if facet_values else None,
                note=note,
            )
        )

    return report


# --------------------------------------------------------------------------
# Разбор округов
# --------------------------------------------------------------------------


def probe_counties(client: httpx.Client, *, pause: float) -> tuple[list[int], list[CountyProbe]]:
    """Установить, что за id отдаёт фасет округов, и как их звать.

    Фасет — голые int, имён в нём нет, поэтому «сопоставить по именам» напрямую
    невозможно. Обходной путь: для каждого id округа берём фасет ``blocks``
    (какие ЖК доступны при этом фильтре) и смотрим поле ``county`` у этих ЖК в
    нашем ``complexes.json``. Имя округа выводится большинством голосов.
    """
    _, base_stats = fetch_count(client, BASIS_MOSCOW)
    facet = dig(base_stats, ("district", "districtCounties")) or []
    facet_ids = [int(v) for v in facet]

    complexes = {e.id: e for e in load_all().complexes}
    registry = fetch_block_registry(client)

    probes: list[CountyProbe] = []
    for facet_id in facet_ids:
        time.sleep(pause)
        params = {**BASIS_MOSCOW, "districtCounties": str(facet_id)}
        count, stats = fetch_count(client, params)
        block_ids = [int(b) for b in (stats.get("blocks") or [])]

        names: Counter[str] = Counter()
        districts: Counter[str] = Counter()
        known = 0
        labels: list[str] = []
        for block_id in block_ids:
            entry = complexes.get(str(block_id))
            labels.append(registry.get(block_id) or f"id={block_id} (нет в реестре)")
            if entry is None:
                continue
            known += 1
            if entry.county:
                names[entry.county] += 1
            if entry.district:
                districts[entry.district] += 1

        best = names.most_common(1)[0][0] if names else None
        probes.append(
            CountyProbe(
                facet_id=facet_id,
                count=count,
                block_ids=block_ids,
                derived_names=dict(names),
                derived_districts=dict(districts),
                best_name=best,
                known_blocks=known,
                block_names=labels,
            )
        )
    return facet_ids, probes


def fetch_block_registry(client: httpx.Client) -> dict[int, str]:
    """Реестр ЖК с именами: ``/api/v1/filter/block``.

    Нужен, чтобы у округов, где наши ЖК не размечены полем ``county``, имя
    можно было опознать хотя бы глазами — по названиям ЖК.
    """
    data = fetch(client, {"types": "1,2"}, endpoint=BLOCK_REGISTRY_ENDPOINT)
    registry: dict[int, str] = {}
    for item in data.get("items") or []:
        block_id = item.get("id")
        if isinstance(block_id, int):
            registry[block_id] = f"{item.get('name')} [{item.get('path')}]"
    return registry


# --------------------------------------------------------------------------
# Печать
# --------------------------------------------------------------------------


def print_kind(report: KindReport) -> None:
    print("=" * 96)
    print(f"{report.title}   параметр `{report.param}`")
    print(f"  baseline (без фильтра) : {report.baseline}")
    print(f"  контрольная проба      : {report.control}")
    facet = report.facet_size if report.facet_size is not None else "—"
    print(f"  значений в фасете      : {facet}   (справка, в вердикт НЕ входит)")
    print(f"  записей в справочнике  : {report.total_entries}, из них без id: {report.without_id}")
    print()

    order = {
        VERDICT_ALIVE: 0,
        VERDICT_DEAD: 1,
        VERDICT_DEAD_OR_INVALID: 1,
        VERDICT_INVALID: 2,
        VERDICT_NOT_NARROWED: 3,
    }
    for verdict in sorted(report.verdicts, key=lambda v: (order.get(v.verdict, 9), v.name)):
        if verdict.verdict == VERDICT_ALIVE:
            continue
        facet_mark = (
            ""
            if verdict.in_facet is None
            else ("  в фасете" if verdict.in_facet else "  нет в фасете")
        )
        note = f"  — {verdict.note}" if verdict.note else ""
        print(
            f"    [{verdict.verdict:<18}] {verdict.name:<32} id={verdict.ref_id:<40} "
            f"count={verdict.count}{facet_mark}{note}"
        )

    tally = report.tally()
    alive = tally.get(VERDICT_ALIVE, 0)
    print()
    print(
        f"  ИТОГО по {report.param}: проверено {len(report.verdicts)} id — "
        + ", ".join(f"{name}: {num}" for name, num in sorted(tally.items(), key=lambda kv: -kv[1]))
    )
    print(f"  (живых {alive} из {len(report.verdicts)})")
    print()


def print_counties(facet_ids: list[int], probes: list[CountyProbe]) -> None:
    print("=" * 96)
    print("РАЗБОР ОКРУГОВ: что за id в фасете и можно ли вывести их имена")
    print(f"  фасет districtCounties: {facet_ids}")
    print()
    for probe in probes:
        derived = ", ".join(
            f"{n}×{c}" for n, c in sorted(probe.derived_names.items(), key=lambda kv: -kv[1])
        )
        districts = ", ".join(
            f"{n}×{c}" for n, c in sorted(probe.derived_districts.items(), key=lambda kv: -kv[1])
        )
        print(
            f"  id={probe.facet_id:<4} count={probe.count:<6} ЖК={len(probe.block_ids):<3} "
            f"наших={probe.known_blocks}"
        )
        print(f"      county из complexes.json : {derived or '— не размечено'}")
        print(f"      district из complexes.json: {districts or '— не размечено'}")
        for label in probe.block_names:
            print(f"        · {label}")
    print()


def print_summary(report: AuditReport) -> None:
    print("=" * 96)
    print("СВОДКА")
    grand: Counter[str] = Counter()
    total = 0
    for kind in report.kinds:
        tally = kind.tally()
        total += len(kind.verdicts)
        grand.update(tally)
        parts = ", ".join(f"{k}: {v}" for k, v in sorted(tally.items(), key=lambda kv: -kv[1]))
        print(f"  {kind.param:<20} {len(kind.verdicts):>4} id — {parts}")
    print(
        f"  {'ВСЕГО':<20} {total:>4} id — "
        + ", ".join(f"{k}: {v}" for k, v in sorted(grand.items(), key=lambda kv: -kv[1]))
    )
    print()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--kinds",
        default="metro,districts,counties,blocks",
        help="какие справочники проверять (через запятую)",
    )
    parser.add_argument("--limit", type=int, default=None, help="не более N id на справочник")
    parser.add_argument("--pause", type=float, default=0.25, help="пауза между запросами, с")
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--confirm", type=int, default=1, help="сколько раз перепроверять нули")
    parser.add_argument("--counties-probe", action="store_true", help="только разбор округов")
    parser.add_argument(
        "--no-counties-probe", action="store_true", help="пропустить разбор округов"
    )
    parser.add_argument(
        "--stub-ua",
        action="store_true",
        help="намеренно послать небраузерный UA — проверка того, что детектор заглушки падает",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    user_agent = STUB_UA if args.stub_ua else BROWSER_UA

    print(f"Эндпоинт : {ENDPOINT}")
    print(f"UA       : {user_agent}")
    if args.stub_ua:
        print("РЕЖИМ ПРОВЕРКИ ДЕТЕКТОРА: ожидается падение, а не таблица нулей.")
    print()

    keys = [k.strip() for k in args.kinds.split(",") if k.strip()]
    unknown = [k for k in keys if k not in KINDS_BY_KEY]
    if unknown:
        print(
            f"неизвестные справочники: {unknown}; доступны: {list(KINDS_BY_KEY)}", file=sys.stderr
        )
        return 2

    reference = load_all()
    report = AuditReport(endpoint=ENDPOINT)

    try:
        with httpx.Client(timeout=args.timeout) as client:
            if args.stub_ua:
                # Единственный запрос: он обязан упереться в детектор.
                fetch(client, BASIS_MOSCOW, user_agent=user_agent)
                print(
                    "ДЕТЕКТОР НЕ СРАБОТАЛ — ответ с небраузерным UA прошёл проверку. "
                    "Это провал проверки: маркер устарел.",
                    file=sys.stderr,
                )
                return 3

            if not args.counties_probe:
                for key in keys:
                    spec = KINDS_BY_KEY[key]
                    entries = getattr(reference, spec.ref_attr)
                    report.kinds.append(
                        audit_kind(
                            client,
                            spec,
                            entries,
                            pause=args.pause,
                            limit=args.limit,
                            confirm=args.confirm,
                        )
                    )
                    print_kind(report.kinds[-1])

            if args.counties_probe or (not args.no_counties_probe and "counties" in keys):
                facet_ids, probes = probe_counties(client, pause=args.pause)
                report.counties_facet = facet_ids
                report.counties_probe = probes
                print_counties(facet_ids, probes)

    except StubResponseError as exc:
        print("\n" + "!" * 96, file=sys.stderr)
        print(f"ЗАГЛУШКА: {exc}", file=sys.stderr)
        print(
            "Ни одна цифра не напечатана намеренно: объявить живые id мёртвыми "
            "по заглушечному ответу — худший исход, чем отсутствие отчёта.",
            file=sys.stderr,
        )
        print("!" * 96, file=sys.stderr)
        return 2
    except ControlProbeError as exc:
        print(f"\nКОНТРОЛЬНАЯ ПРОБА ПРОВАЛЕНА: {exc}", file=sys.stderr)
        return 4

    if report.kinds:
        print_summary(report)

    if args.json_out:
        args.json_out.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
