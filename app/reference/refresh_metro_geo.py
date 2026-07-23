"""Обогащение ``metro.json`` координатами и принадлежностью к линии из OSM.

CLI-скрипт: ``python -m app.reference.refresh_metro_geo``. Как и
:mod:`app.reference.refresh`, это **офлайн-команда**: сеть здесь допустима,
рантайм запроса ею не пользуется (архитектурный инвариант №4 — справочники
читаются только с диска). В ``REFRESHABLE`` основного ``refresh.py`` этот
скрипт намеренно **не входит**: источники разные (api.pik.ru vs OSM), запускать
их надо независимо.

Зачем: у записей ``metro.json`` не было ``lat``/``lon``, поэтому «рядом с метро
Тушинская» нельзя было превратить в детерминированное сужение списка ЖК по
расстоянию — так, как это уже работает для именованных ориентиров
(``landmarks.json`` + :mod:`app.geo.candidates`). Координаты станции — такой же
объективный факт, как координаты ЖК: haversine считается математикой, ИИ здесь
не нужен.

Источник — OSM Overpass API. Берём три сети (все — маршрутные отношения,
``type=route``):

- метро: ``route=subway``, ``network="Московский метрополитен"``;
- МЦК: ``route=train``, ``ref=14``, та же ``network`` (в OSM МЦК — не
  ``light_rail``, проверено на живых данных);
- МЦД: ``route=train``, ``network="МЦД"`` (D1…D5 + ветки вида ``D4А``).

Станция берётся из членов отношения (узлы с именем и ``railway``/
``public_transport``), линия — из тегов самого отношения. Пересадочный узел
законно принадлежит нескольким линиям, поэтому ``line`` — все найденные линии
через ``" / "`` в стабильном порядке (ничего не отбрасываем молча, инвариант №3).

Мёрж — по образцу :func:`app.reference.refresh.merge_entries`: кураторские
``slug``/``id``/``aliases``/``name`` не затираются, обновляются только
``lat``/``lon``/``line``, сортировка стабильная (диффы читаемы). Станции,
которых в справочнике ещё нет (в первую очередь МЦД), добавляются новыми
записями **без выдуманных** ``slug``/``id`` — эти поля докуриваются вручную
(то же известное ограничение, что и с GUID-ами станций).
"""

import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from rapidfuzz import fuzz

from app.reference.loader import DATA_DIR, REFERENCE_FILES, RefEntry, normalize
from app.reference.refresh import load_existing, write_entries

#: Публичный Overpass-эндпоинт (тот же, что использует гео-слой POI).
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

#: Временные отказы публичного Overpass (перегрузка/лимит), которые имеет смысл ретраить.
RETRYABLE_STATUS = frozenset({429, 502, 503, 504})
RETRY_ATTEMPTS = 5
RETRY_SLEEP_SECONDS = 30.0

#: Bbox Москвы (метро + МЦК) и расширенный bbox агломерации (МЦД уходят в область).
MOSCOW_BBOX = (55.40, 36.70, 56.05, 38.30)
MCD_BBOX = (55.00, 36.30, 56.40, 38.90)

#: Значение тега ``network`` у московского метро и МЦК в OSM.
SUBWAY_NETWORK = "Московский метрополитен"
#: Значение тега ``network`` у Московских центральных диаметров.
MCD_NETWORK = "МЦД"
#: ``ref`` маршрута МЦК (официально — 14-я линия метро).
MCC_REF = "14"

#: Теги, по которым член отношения признаётся станцией (а не рельсом/входом).
_STATION_RAILWAY = {"station", "halt", "stop", "stop_position", "tram_stop"}
_STATION_PT = {"stop_position", "station", "platform"}

#: Порог нечёткого сопоставления имени OSM-станции с записью справочника.
#: Держим высоко намеренно: лучше не сматчить (запись просто добавится новой),
#: чем приписать координаты чужой станции («Волоколамская» vs «Волоколамск»).
FUZZY_THRESHOLD = 93.0
#: Короткие имена нечётко не сравниваем — на них порог достигается случайно
#: (та же логика, что в ``entity_match.SHORT_ENTITY_EXACT_MAX_LEN``).
FUZZY_MIN_LEN = 6

#: Разделитель списка линий у пересадочного узла.
LINE_SEPARATOR = " / "

_MCD_REF_RE = re.compile(r"^D(\d+)", re.IGNORECASE)


def build_query() -> str:
    """Собрать Overpass-запрос: маршруты метро/МЦК/МЦД и их узлы-станции."""
    m = ",".join(f"{coord:.2f}" for coord in MOSCOW_BBOX)
    d = ",".join(f"{coord:.2f}" for coord in MCD_BBOX)
    return (
        "[out:json][timeout:300];\n"
        "(\n"
        f'  relation["route"="subway"]["network"="{SUBWAY_NETWORK}"]({m});\n'
        f'  relation["route"="train"]["ref"="{MCC_REF}"]["network"="{SUBWAY_NETWORK}"]({m});\n'
        f'  relation["route"="train"]["network"="{MCD_NETWORK}"]({d});\n'
        ");\n"
        "out body;\n"
        "node(r);\n"
        "out body;"
    )


def fetch_overpass(client: httpx.Client, attempts: int = RETRY_ATTEMPTS) -> dict[str, Any]:
    """Выполнить запрос к Overpass и вернуть распарсенный JSON.

    Публичный Overpass регулярно отвечает 429/504 при нагрузке — это временный
    отказ, а не ошибка запроса, поэтому такие коды ретраятся с паузой
    (:data:`RETRY_SLEEP_SECONDS`). Прочие коды поднимаются сразу.
    """
    response = client.post(OVERPASS_URL, data={"data": build_query()})
    for _ in range(max(attempts - 1, 0)):
        if response.status_code not in RETRYABLE_STATUS:
            break
        time.sleep(RETRY_SLEEP_SECONDS)
        response = client.post(OVERPASS_URL, data={"data": build_query()})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or "elements" not in payload:
        raise ValueError(f"Неожиданный ответ {OVERPASS_URL}: нет поля elements")
    return payload


def line_label(tags: dict[str, str]) -> str | None:
    """Человекочитаемое имя линии из тегов маршрутного отношения.

    - МЦД: ``ref=D2`` / ``D4А`` → ``«МЦД-2»`` / ``«МЦД-4»`` (ветка сводится к
      своему диаметру — для пользователя это одна и та же линия);
    - МЦК: ``ref=14`` в сети метро → ``«МЦК»``;
    - метро: имя маршрута до ``«:»``/``« (»`` без хвоста ``« линия»``
      (``«Сокольническая линия: … → …»`` → ``«Сокольническая»``).
    """
    network = (tags.get("network") or "").strip()
    ref = (tags.get("ref") or "").strip()
    route = (tags.get("route") or "").strip()

    if network == MCD_NETWORK:
        match = _MCD_REF_RE.match(ref)
        return f"МЦД-{match.group(1)}" if match else None
    if route == "train" and ref == MCC_REF:
        return "МЦК"
    if route != "subway":
        return None

    name = (tags.get("name") or "").strip()
    if not name:
        return None
    name = re.split(r"\s*[:(]", name, maxsplit=1)[0].strip()
    name = re.sub(r"\s+линия$", "", name, flags=re.IGNORECASE).strip()
    return name or None


def _is_station_node(tags: dict[str, str]) -> bool:
    """Узел-член маршрута описывает станцию (а не рельс/вход/светофор)."""
    if not tags.get("name"):
        return False
    return tags.get("railway") in _STATION_RAILWAY or tags.get("public_transport") in _STATION_PT


def _node_aliases(tags: dict[str, str]) -> tuple[str, ...]:
    """Алиасы из реальных тегов OSM (``alt_name``/``short_name``), без выдумок."""
    aliases: list[str] = []
    for key in ("alt_name", "short_name"):
        raw = tags.get(key)
        if not raw:
            continue
        for part in raw.split(";"):
            candidate = part.strip()
            if candidate and normalize(candidate) != normalize(tags["name"]):
                aliases.append(candidate)
    seen: set[str] = set()
    return tuple(a for a in aliases if not (normalize(a) in seen or seen.add(normalize(a))))


def parse_stations(payload: dict[str, Any]) -> list[RefEntry]:
    """Превратить ответ Overpass в записи справочника (name/lat/lon/line/aliases).

    Станции агрегируются по нормализованному имени: пересадочный узел
    встречается в нескольких маршрутах, его ``line`` — объединение линий,
    координаты — от узла с наименьшим OSM-id (детерминированно).
    """
    elements = payload.get("elements", [])
    nodes: dict[int, dict[str, Any]] = {
        el["id"]: el for el in elements if el.get("type") == "node" and "id" in el
    }

    #: normalized name -> (name, best_node_id, lat, lon, lines, aliases)
    collected: dict[str, dict[str, Any]] = {}

    for el in elements:
        if el.get("type") != "relation":
            continue
        label = line_label(el.get("tags") or {})
        if not label:
            continue
        for member in el.get("members") or []:
            if member.get("type") != "node":
                continue
            node = nodes.get(member.get("ref"))
            if node is None:
                continue
            tags = node.get("tags") or {}
            if not _is_station_node(tags):
                continue
            lat, lon = node.get("lat"), node.get("lon")
            if lat is None or lon is None:
                continue
            name = tags["name"].strip()
            key = normalize(name)
            record = collected.get(key)
            if record is None:
                collected[key] = {
                    "name": name,
                    "node_id": node["id"],
                    "lat": lat,
                    "lon": lon,
                    "lines": {label},
                    "aliases": list(_node_aliases(tags)),
                }
                continue
            record["lines"].add(label)
            for alias in _node_aliases(tags):
                if normalize(alias) not in {normalize(a) for a in record["aliases"]}:
                    record["aliases"].append(alias)
            if node["id"] < record["node_id"]:
                record.update(node_id=node["id"], lat=lat, lon=lon, name=name)

    entries = [
        RefEntry(
            name=record["name"],
            lat=record["lat"],
            lon=record["lon"],
            line=LINE_SEPARATOR.join(sorted(record["lines"])),
            aliases=tuple(record["aliases"]),
        )
        for record in collected.values()
    ]
    return sorted(entries, key=lambda entry: normalize(entry.name))


#: Приоритеты сопоставления OSM-станции с записью справочника: чем меньше —
#: тем надёжнее. Нужны, когда на одну кураторскую запись претендуют несколько
#: станций (например, у «Аэропорт Внуково» есть алиас «Внуково», а в OSM это
#: две разные станции): координаты берутся у совпадения с наивысшим приоритетом.
MATCH_BY_NAME = 0
MATCH_BY_ALIAS = 1
MATCH_BY_FUZZY = 2


def _match_indexes(entries: list[RefEntry]) -> tuple[dict[str, int], dict[str, int]]:
    """Индексы для сопоставления: точный по именам и точный по алиасам.

    Имена и алиасы разведены намеренно: совпадение по имени всегда надёжнее
    совпадения по чужому алиасу, а один общий словарь ставил бы их в
    зависимость от порядка записей в файле.
    """
    by_name: dict[str, int] = {}
    by_alias: dict[str, int] = {}
    for position, entry in enumerate(entries):
        by_name.setdefault(normalize(entry.name), position)
    for position, entry in enumerate(entries):
        for alias in entry.aliases:
            key = normalize(alias)
            if key not in by_name:
                by_alias.setdefault(key, position)
    return by_name, by_alias


def _fuzzy_index(entries: list[RefEntry]) -> list[tuple[str, int]]:
    """Плоский список (нормализованное имя/алиас, индекс записи) для rapidfuzz."""
    index: list[tuple[str, int]] = []
    for position, entry in enumerate(entries):
        index.append((normalize(entry.name), position))
        index.extend((normalize(alias), position) for alias in entry.aliases)
    return index


def _match_position(
    needle: str,
    by_name: dict[str, int],
    by_alias: dict[str, int],
    fuzzy_index: list[tuple[str, int]],
) -> tuple[int, int] | None:
    """Найти запись справочника для имени станции: точно, затем нечётко."""
    if needle in by_name:
        return by_name[needle], MATCH_BY_NAME
    if needle in by_alias:
        return by_alias[needle], MATCH_BY_ALIAS
    if len(needle) < FUZZY_MIN_LEN:
        return None
    best_score = FUZZY_THRESHOLD
    position: int | None = None
    for candidate, candidate_position in fuzzy_index:
        if len(candidate) < FUZZY_MIN_LEN:
            continue
        score = fuzz.QRatio(needle, candidate)
        if score >= best_score:
            best_score, position = score, candidate_position
    return (position, MATCH_BY_FUZZY) if position is not None else None


def _split_lines(line: str | None) -> set[str]:
    """Разобрать поле ``line`` обратно в множество линий."""
    return {part.strip() for part in (line or "").split(LINE_SEPARATOR) if part.strip()}


def merge_metro(existing: list[RefEntry], fetched: list[RefEntry]) -> tuple[list[RefEntry], int]:
    """Смёржить гео-данные OSM в справочник станций, ничего кураторского не теряя.

    Сопоставление — точное по имени, затем по алиасу, затем нечёткое (rapidfuzz
    ``QRatio``, порог :data:`FUZZY_THRESHOLD`). Обновляются только
    ``lat``/``lon``/``line``; ``name``/``slug``/``id``/``aliases`` кураторские.
    Несопоставленные станции добавляются новыми записями без ``slug``/``id``.

    Если на одну запись справочника претендует несколько станций OSM, линии
    объединяются (ничего не теряем молча), а координаты берутся у совпадения с
    лучшим приоритетом (:data:`MATCH_BY_NAME` > алиас > fuzzy).

    Возвращает ``(записи, число добавленных)``; порядок стабилен (по имени).
    """
    merged = list(existing)
    by_name, by_alias = _match_indexes(merged)
    fuzzy_index = _fuzzy_index(merged)

    updates: dict[int, dict[str, Any]] = {}
    added = 0

    for entry in fetched:
        matched = _match_position(normalize(entry.name), by_name, by_alias, fuzzy_index)
        if matched is None:
            merged.append(entry)
            added += 1
            continue
        position, priority = matched
        update = updates.setdefault(
            position, {"lines": _split_lines(merged[position].line), "priority": None}
        )
        update["lines"] |= _split_lines(entry.line)
        if entry.lat is not None and (update["priority"] is None or priority < update["priority"]):
            update.update(priority=priority, lat=entry.lat, lon=entry.lon)

    for position, update in updates.items():
        current = merged[position]
        merged[position] = current.model_copy(
            update={
                "lat": update.get("lat", current.lat),
                "lon": update.get("lon", current.lon),
                "line": LINE_SEPARATOR.join(sorted(update["lines"])) or current.line,
            }
        )

    return sorted(merged, key=lambda item: (normalize(item.name), item.slug or "")), added


def refresh_metro_geo(client: httpx.Client, data_dir: Path = DATA_DIR) -> dict[str, int]:
    """Скачать станции из OSM и обогатить ``metro.json``; вернуть сводку."""
    payload = fetch_overpass(client)
    fetched = parse_stations(payload)

    path = data_dir / REFERENCE_FILES["metro"]
    merged, added = merge_metro(load_existing(path), fetched)
    write_entries(path, merged)

    return {
        "fetched": len(fetched),
        "total": len(merged),
        "added": added,
        "with_coords": sum(1 for entry in merged if entry.lat is not None),
        "with_line": sum(1 for entry in merged if entry.line),
        "mcd": sum(1 for entry in merged if entry.line and "МЦД" in entry.line),
    }


def main() -> int:
    """Точка входа CLI: сходить в Overpass, перезаписать metro.json, напечатать сводку."""
    headers = {"User-Agent": "picurl-metro-geo/0.1 (+https://github.com/stefan200707/picurl)"}
    with httpx.Client(timeout=300, headers=headers) as client:
        counts = refresh_metro_geo(client)

    print(f"{REFERENCE_FILES['metro']}: {counts['total']} записей")
    print(f"  станций получено из OSM: {counts['fetched']}")
    print(f"  добавлено новых записей: {counts['added']}")
    print(f"  с координатами: {counts['with_coords']}")
    print(f"  с линией: {counts['with_line']}")
    print(f"  из них МЦД: {counts['mcd']}")
    print(
        "Напоминание: slug/GUID станций OSM не даёт — они по-прежнему "
        "докуриваются вручную (см. CLAUDE.md, раздел «Справочники»)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
