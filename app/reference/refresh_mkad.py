"""Пересборка полигона МКАД (``mkad_ring.json``) из OSM Overpass.

CLI: ``python -m app.reference.refresh_mkad``. Как и
:mod:`app.reference.refresh_metro_geo`, это **офлайн-команда**: сеть здесь
допустима, рантайм запроса ею не пользуется (инвариант №6 — справочники читаются
только с диска). В ``REFRESHABLE`` основного ``refresh.py`` НЕ входит.

Зачем: у pik.ru нет URL-фильтра «внутри МКАД» (docs/pik-url-schema.md), поэтому
желание реализуется гео-сужением ЖК по ``blocks`` (``app.pik.location_fallback``
+ ``app.geo.mkad.point_in_mkad``). Точный контур кольца — из OSM.

ВАЖНО: закоммиченный снапшот ``mkad_ring.json`` — документированное **приближение**
(эллипс по опубликованному bbox кольца). Этот скрипт заменяет его точным контуром,
КОГДА доступно полноценное (full-planet) зеркало Overpass. Публичные зеркала под
нагрузкой отдают 4xx/5xx/таймауты и иногда блокируют запросы без User-Agent —
поэтому шлём явный UA и ретраим транзиент (как в refresh_metro_geo).

Источник — маршрутное/дорожное представление МКАД: собираем геометрию всех
member-way'ев в один упорядоченный замкнутый контур (наибольший кольцевой
компонент). Кураторский комментарий/поля файла сохраняются.
"""

import json
import sys
import time
from typing import Any

import httpx

from app.geo.mkad import MKAD_RING_FILE

#: Полноценные (full-planet) зеркала. Региональные (overpass.osm.ch) на
#: московские координаты молча отвечают count=0 — использовать нельзя (CLAUDE.md).
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
#: Явный User-Agent: часть зеркал отвечает 406/429 на запросы без него.
_HEADERS = {"User-Agent": "picurl-refresh-mkad/1.0 (reference data refresh)"}

RETRYABLE_STATUS = frozenset({406, 429, 502, 503, 504})
RETRY_ATTEMPTS = 4
RETRY_SLEEP_SECONDS = 20.0

#: МКАД как дорожное отношение по официальному имени.
_MKAD_NAME = "Московская кольцевая автомобильная дорога"
_QUERY = f'[out:json][timeout:90];relation["name"="{_MKAD_NAME}"];out geom;'


def _fetch() -> dict[str, Any]:
    """Запросить геометрию МКАД, перебирая зеркала и ретрая транзиент."""
    last_error: str | None = None
    for url in OVERPASS_MIRRORS:
        for _attempt in range(RETRY_ATTEMPTS):
            try:
                resp = httpx.post(
                    url, data={"data": _QUERY.encode("utf-8")}, headers=_HEADERS, timeout=95
                )
                if resp.status_code == 200:
                    return resp.json()
                last_error = f"{url}: HTTP {resp.status_code}"
                if resp.status_code not in RETRYABLE_STATUS:
                    break
            except httpx.HTTPError as exc:  # транзиент сети — ретраим
                last_error = f"{url}: {exc!r}"
            time.sleep(RETRY_SLEEP_SECONDS)
    raise RuntimeError(f"Overpass недоступен: {last_error}")


def _assemble_ring(data: dict[str, Any]) -> list[list[float]]:
    """Собрать упорядоченный замкнутый контур [lon, lat] из member-way'ев.

    Way'и отношения соединяются по общим концам в кольцо; берётся наибольший
    связный компонент. Простая жадная стыковка — контур МКАД замкнут в OSM.
    """
    ways: list[list[tuple[float, float]]] = []
    for element in data.get("elements", []):
        for member in element.get("members", []):
            geom = member.get("geometry")
            if geom:
                ways.append([(p["lon"], p["lat"]) for p in geom])
    if not ways:
        raise RuntimeError("OSM не вернул геометрию МКАД (0 way-сегментов)")

    ordered: list[tuple[float, float]] = list(ways.pop(0))
    changed = True
    while ways and changed:
        changed = False
        for i, way in enumerate(ways):
            if _close(ordered[-1], way[0]):
                ordered.extend(way[1:])
            elif _close(ordered[-1], way[-1]):
                ordered.extend(reversed(way[:-1]))
            elif _close(ordered[0], way[-1]):
                ordered[:0] = way[:-1]
            elif _close(ordered[0], way[0]):
                ordered[:0] = list(reversed(way))[:-1]
            else:
                continue
            ways.pop(i)
            changed = True
            break

    if not _close(ordered[0], ordered[-1]):
        ordered.append(ordered[0])  # замкнуть на всякий случай
    return [[round(lon, 6), round(lat, 6)] for lon, lat in ordered]


def _close(a: tuple[float, float], b: tuple[float, float], eps: float = 1e-6) -> bool:
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def refresh_mkad() -> None:
    data = _fetch()
    ring = _assemble_ring(data)
    doc = {
        "_comment": (
            "Контур границы МКАД из OSM (Московская кольцевая автомобильная дорога). "
            "Пересобрать: python -m app.reference.refresh_mkad."
        ),
        "source": "OSM Overpass (relation name=Московская кольцевая автомобильная дорога)",
        "ring_lonlat": ring,
    }
    MKAD_RING_FILE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Обновлён {MKAD_RING_FILE}: {len(ring)} точек контура")


if __name__ == "__main__":
    try:
        refresh_mkad()
    except Exception as exc:
        print(f"Не удалось обновить полигон МКАД: {exc}", file=sys.stderr)
        sys.exit(1)
