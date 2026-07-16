import re
from typing import NamedTuple

from .core import _NUM, Span, _iter_free, _normalize, _to_number

# --- Площадь (общая и кухни) ---


class AreaFacts(NamedTuple):
    """Границы площади в м² (None = не задано)."""

    area_min: float | None = None
    area_max: float | None = None
    area_kitchen_min: float | None = None
    area_kitchen_max: float | None = None


_AREA_UNIT = r"(?:м²|м2|кв\.?\s*метр\w*|кв\.?\s*м\.?|квадрат\w*|метр\w*)"
#: Связки между ключевым словом и числом: «кухня чтоб большая от 12», «кухня была от 12».
_AREA_FILLER = r"(?:(?:была|будет|есть|чтоб\w*|больш\w*|маленьк\w*)\s+)*"

#: «кухня от 8», «с кухней от 10 метров», «кухня 8-12», «кухня 10 м²», «кухня была от 12»,
#: «кузня от 9 до 19».
_KITCHEN = re.compile(
    rf"(?:с\s+)?\b(?:кухн\w*|кузн\w*)\s*{_AREA_FILLER}[—:\-]?\s*"
    rf"(?:"
    rf"(?:от\s+)?({_NUM})\s*(?:[-–—]|до)\s*({_NUM})"  # 1, 2: диапазон
    rf"|(?:от|не\s+меньше|минимум)\s+({_NUM})"  # 3: min
    rf"|(?:до|не\s+больше|максимум)\s+({_NUM})"  # 4: max
    rf"|({_NUM})"  # 5: точное значение → min
    rf")"
    rf"(?:\s*{_AREA_UNIT})?"
)
#: «площадь от 40», «общей площадью 50-70», «общая от 60», «квадратов 60», «от 9 до 19 кв м».
_AREA_KEYWORD = re.compile(
    rf"(?:(?:общ\w+\s+)?\bплощад\w*(?:\s+общ\w+)?|\bобщ\w+|\bквадрат\w*)\s*{_AREA_FILLER}[—:\-]?\s*"
    rf"(?:"
    rf"(?:от\s+)?({_NUM})\s*(?:[-–—]|до)\s*({_NUM})"  # 1, 2: диапазон
    rf"|(?:от|не\s+меньше|минимум)\s+({_NUM})"  # 3: min
    rf"|(?:до|не\s+больше|максимум)\s+({_NUM})"  # 4: max
    rf"|({_NUM})"  # 5: точное значение → min
    rf")"
    rf"(?:\s*{_AREA_UNIT})?"
)
#: «70-100 метров», «от 40 до 60 м²» — единица обязательна.
_AREA_RANGE = re.compile(rf"\b(?:от\s+)?({_NUM})\s*(?:[-–—]|до)\s*({_NUM})\s*{_AREA_UNIT}")
_AREA_MIN = re.compile(rf"\b(?:от|не\s+меньше|минимум)\s+({_NUM})\s*{_AREA_UNIT}")
_AREA_MAX = re.compile(rf"\b(?:до|не\s+больше|максимум)\s+({_NUM})\s*{_AREA_UNIT}")
_AREA_KVADRATOV = re.compile(rf"\b({_NUM})\s*квадрат\w*|\bквадрат\w*\s+({_NUM})\b")


def _area_bounds(match: re.Match[str]) -> tuple[float | None, float | None]:
    """Разобрать группы _KITCHEN/_AREA_KEYWORD в пару (min, max)."""
    g_lo, g_hi, g_min, g_max, g_exact = match.group(1, 2, 3, 4, 5)
    if g_lo is not None and g_hi is not None:
        return _to_number(g_lo), _to_number(g_hi)
    if g_min is not None:
        return _to_number(g_min), None
    if g_max is not None:
        return None, _to_number(g_max)
    if g_exact is not None:
        return _to_number(g_exact), None
    return None, None


def extract_area(text: str) -> tuple[AreaFacts, list[Span]]:
    """Извлечь площадь: общую и кухни (различаются по ключевым словам)."""
    norm = _normalize(text)
    spans: list[Span] = []
    area_min: float | None = None
    area_max: float | None = None
    kitchen_min: float | None = None
    kitchen_max: float | None = None

    # Кухня — первой: «кухня от 8 м²» не должна попасть в общую площадь.
    for match in _iter_free(_KITCHEN, norm, spans):
        low, high = _area_bounds(match)
        if low is None and high is None:
            continue
        if kitchen_min is None and low is not None:
            kitchen_min = low
        if kitchen_max is None and high is not None:
            kitchen_max = high
        spans.append(match.span())

    for pattern in (_AREA_KEYWORD, _AREA_RANGE, _AREA_MIN, _AREA_MAX, _AREA_KVADRATOV):
        for match in _iter_free(pattern, norm, spans):
            if pattern in (_AREA_KEYWORD, _KITCHEN):
                low, high = _area_bounds(match)
            elif pattern is _AREA_RANGE:
                low, high = _to_number(match.group(1)), _to_number(match.group(2))
            elif pattern is _AREA_MIN:
                low, high = _to_number(match.group(1)), None
            elif pattern is _AREA_KVADRATOV:
                val = match.group(1) if match.group(1) is not None else match.group(2)
                low, high = _to_number(val), None
            else:
                low, high = None, _to_number(match.group(1))
            if low is None and high is None:
                continue
            if area_min is None and low is not None:
                area_min = low
            if area_max is None and high is not None:
                area_max = high
            spans.append(match.span())

    return AreaFacts(area_min, area_max, kitchen_min, kitchen_max), sorted(spans)
