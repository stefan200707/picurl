"""Проверка «точка внутри МКАД» для гео-сужения «внутри/за МКАД».

pik.ru не имеет URL-фильтра «внутри МКАД» (см. docs/pik-url-schema.md — только
округ/метро/район/ЖК). Поэтому желание «внутри/за МКАД» реализуется сужением
списка ЖК по ``blocks`` (единственный проверяемый параметр,
``app.pik.location_fallback``): берём ЖК, чьи координаты лежат внутри (или вне)
полигона кольца МКАД.

Полигон — ``app/reference/mkad_ring.json`` (список ``[lon, lat]``, замкнутый).
Runtime читает его с диска (инвариант №6, сеть не трогаем); пересобрать из
официального источника (OSM) — ``python -m app.reference.refresh_mkad``. Текущий
снапшот — документированное приближение (эллипс по bbox кольца): для best-effort
сужения этого достаточно, а результат честно помечается как «приближение» в
заметках фолбэка.
"""

from __future__ import annotations

import json
from functools import cache

from app.reference.loader import DATA_DIR

MKAD_RING_FILE = DATA_DIR / "mkad_ring.json"


@cache
def load_mkad_ring() -> list[tuple[float, float]]:
    """Полигон кольца МКАД как список ``(lat, lon)`` (замкнутый). Кэшируется."""
    raw = json.loads(MKAD_RING_FILE.read_text(encoding="utf-8"))
    # В файле точки хранятся как [lon, lat] (гео-конвенция GeoJSON).
    return [(lat, lon) for lon, lat in raw["ring_lonlat"]]


def point_in_mkad(lat: float, lon: float) -> bool:
    """Лежит ли точка внутри полигона МКАД (алгоритм ray-casting).

    Чистая геометрия, без внешних зависимостей. Граница трактуется
    консервативно — точная классификация пограничных ЖК не критична для
    best-effort сужения.
    """
    ring = load_mkad_ring()
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]
        # Луч по долготе на широте `lat`: пересекает ли ребро (i, j)?
        if (lat_i > lat) != (lat_j > lat):
            lon_at_lat = lon_i + (lat - lat_i) / (lat_j - lat_i) * (lon_j - lon_i)
            if lon < lon_at_lat:
                inside = not inside
        j = i
    return inside
