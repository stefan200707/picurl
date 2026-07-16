import re
from typing import NamedTuple

from .core import Span, _iter_free, _normalize


class FloorFacts(NamedTuple):
    """Ограничения по этажу."""

    floor_min: int | None = None
    floor_max: int | None = None
    not_first_floor: bool = False
    last_floor: bool = False
    not_last_floor: bool = False


_FLOOR_RANGE_A = re.compile(r"\b(?:с|от)\s+(\d+)\s+(?:по|до)\s+(\d+)\s*(?:-?го)?\s*этаж\w*")
_FLOOR_RANGE_B = re.compile(r"\bэтаж\w*\s*[—:\-]?\s*(?:с|от)\s+(\d+)\s+(?:по|до)\s+(\d+)")
_FLOOR_RANGE_C = re.compile(r"\b(\d+)\s*[-–—]\s*(\d+)\s+этаж\w*")
_FLOOR_RANGE_D = re.compile(r"\bэтаж\w*\s*[—:\-]?\s*(\d+)\s*[-–—]\s*(\d+)")
_FLOOR_RANGE_E = re.compile(r"\b(?:с|от)\s+(\d+)\s+(?:по|до)\s+(\d+)\b")
_FLOOR_MIN = re.compile(r"\b(?:не\s+ниже|от|начиная\s+с|с)\s+(\d+)(?:-?го)?\s+этаж\w*")
_FLOOR_MAX = re.compile(r"\b(?:не\s+выше|до)\s+(\d+)(?:-?го)?\s+этаж\w*")
_FLOOR_NOT_FIRST = re.compile(r"\b(?:не\s+(?:на\s+)?|кроме\s+|выше\s+)перв\w+(?:\s+этаж\w*)?")
_FLOOR_HIGH = re.compile(
    r"\bвысок\w+\s+этаж\w*|\bэтаж\w*\s+высок\w+|\bэтаж\w*\s+повыше|\bповыше\s+этаж\w*"
)
_FLOOR_NOT_LAST = re.compile(r"\b(?:не\s+|кроме\s+)(?:на\s+)?последн\w+(?:\s+этаж\w*)?")
_FLOOR_LAST = re.compile(r"\b(?:на\s+)?последн\w+(?:\s+этаж\w*)?")
#: Отрицание перед «последний …» — «не последний этаж» не должен дать last_floor.
_NEGATION_BEFORE = re.compile(r"(?:\bне|\bбез|\bтолько\s+не)\s+$")


def extract_floor(text: str) -> tuple[FloorFacts, list[Span]]:
    """Извлечь ограничения по этажу («с 5 по 20», «не первый», «последний»)."""
    norm = _normalize(text)
    spans: list[Span] = []
    floor_min: int | None = None
    floor_max: int | None = None
    not_first = False
    last = False
    not_last = False

    for pattern in (_FLOOR_RANGE_A, _FLOOR_RANGE_B, _FLOOR_RANGE_C, _FLOOR_RANGE_D, _FLOOR_RANGE_E):
        for match in _iter_free(pattern, norm, spans):
            f_min = int(match.group(1))
            f_max = int(match.group(2))
            if f_min > 200 or f_max > 200:
                continue
            if floor_min is None:
                floor_min = f_min
            if floor_max is None:
                floor_max = f_max
            spans.append(match.span())

    for match in _iter_free(_FLOOR_MIN, norm, spans):
        if floor_min is None:
            floor_min = int(match.group(1))
            spans.append(match.span())

    for match in _iter_free(_FLOOR_MAX, norm, spans):
        if floor_max is None:
            floor_max = int(match.group(1))
            spans.append(match.span())

    for match in _iter_free(_FLOOR_NOT_FIRST, norm, spans):
        not_first = True
        spans.append(match.span())

    for match in _iter_free(_FLOOR_HIGH, norm, spans):
        not_first = True
        spans.append(match.span())

    for match in _iter_free(_FLOOR_NOT_LAST, norm, spans):
        not_last = True
        spans.append(match.span())

    for match in _iter_free(_FLOOR_LAST, norm, spans):
        if _NEGATION_BEFORE.search(norm[: match.start()]):
            continue  # «не последний этаж» — в URL не выражается, уйдёт в warnings
        last = True
        spans.append(match.span())

    return FloorFacts(floor_min, floor_max, not_first, last, not_last), sorted(spans)
