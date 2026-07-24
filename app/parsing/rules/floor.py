import re
from collections.abc import Iterable
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
_FLOOR_MIN = re.compile(r"\b(?:не\s+ниже|от|начиная\s+с|с|выше)\s+(\d+)(?:-?го)?\s+этаж\w*")
_FLOOR_MAX = re.compile(r"\b(?:не\s+выше|до)\s+(\d+)(?:-?го)?\s+этаж\w*")
_FLOOR_NOT_FIRST = re.compile(r"\b(?:не\s+(?:на\s+)?|кроме\s+|выше\s+)перв\w+(?:\s+этаж\w*)?")
#: Постфиксное отрицание: «первый этаж не предлагать / не надо / исключить».
#: Отрицание стоит ПОСЛЕ «перв… этаж», поэтому _FLOOR_NOT_FIRST (префиксное) его
#: не ловит. Обрабатывается ДО позитивного _FLOOR_FIRST и съедает его span.
_FLOOR_NOT_FIRST_POST = re.compile(
    r"\bперв\w+\s+этаж\w*\s+"
    r"(?:не\s+(?:предлаг\w+|надо|нужн\w+|интересу\w+|хоч\w+|рассматрив\w+|показыв\w+)|исключ\w+)"
)
#: Позитивное «первый этаж» → floor_min=floor_max=1 (пользователь ХОЧЕТ первый).
#: Срабатывает только если span не съеден отрицанием (префиксным/постфиксным).
_FLOOR_FIRST = re.compile(r"\bперв\w+\s+этаж\w*")
_FLOOR_HIGH = re.compile(
    r"\bвысок\w+\s+этаж\w*|\bэтаж\w*\s+высок\w+|\bэтаж\w*\s+повыше|\bповыше\s+этаж\w*"
)
_FLOOR_NOT_LAST = re.compile(
    r"\b(?:не|без|только\s+не|кроме)\s+(?:на\s+)?последн\w+(?:\s+этаж\w*)?"
)
_FLOOR_LAST = re.compile(r"\b(?:на\s+)?последн\w+(?:\s+этаж\w*)?")


def extract_floor(
    text: str, consumed: Iterable[Span] | None = None
) -> tuple[FloorFacts, list[Span]]:
    """Извлечь ограничения по этажу («с 5 по 20», «не первый», «последний»).

    ``consumed`` — диапазоны, уже съеденные другими правилами (цена/площадь/время).
    Голый паттерн диапазона (_FLOOR_RANGE_E, «от X до Y» без слова «этаж») иначе
    повторно матчит эти числа и подставляет паразитный этаж; здесь он пропускается
    при пересечении. Собственные же спаны этажа остаются в возвращаемом списке.
    """
    norm = _normalize(text)
    blocked: list[Span] = list(consumed) if consumed else []
    spans: list[Span] = []

    def _free(pattern: re.Pattern[str]):
        return _iter_free(pattern, norm, [*spans, *blocked])

    floor_min: int | None = None
    floor_max: int | None = None
    not_first = False
    last = False
    not_last = False

    for pattern in (_FLOOR_RANGE_A, _FLOOR_RANGE_B, _FLOOR_RANGE_C, _FLOOR_RANGE_D, _FLOOR_RANGE_E):
        for match in _free(pattern):
            f_min = int(match.group(1))
            f_max = int(match.group(2))
            if f_min > 200 or f_max > 200:
                continue
            if floor_min is None:
                floor_min = f_min
            if floor_max is None:
                floor_max = f_max
            spans.append(match.span())

    for match in _free(_FLOOR_MAX):
        if floor_max is None:
            floor_max = int(match.group(1))
            spans.append(match.span())

    for match in _free(_FLOOR_MIN):
        if floor_min is None:
            floor_min = int(match.group(1))
            spans.append(match.span())

    for match in _free(_FLOOR_NOT_FIRST):
        not_first = True
        spans.append(match.span())

    # Постфиксное отрицание «первый этаж не предлагать» — ДО позитивного _FLOOR_FIRST,
    # чтобы съесть его span и не выставить floor=1.
    for match in _free(_FLOOR_NOT_FIRST_POST):
        not_first = True
        spans.append(match.span())

    for match in _free(_FLOOR_HIGH):
        not_first = True
        spans.append(match.span())

    for match in _free(_FLOOR_NOT_LAST):
        not_last = True
        spans.append(match.span())

    for match in _free(_FLOOR_LAST):
        last = True
        spans.append(match.span())

    # Позитивный первый этаж — последним: любые отрицания «перв…» выше уже съели
    # свой span, поэтому оставшиеся «первый этаж» — это реальное пожелание.
    for match in _free(_FLOOR_FIRST):
        if floor_min is None and floor_max is None and not not_first:
            floor_min = 1
            floor_max = 1
            spans.append(match.span())

    return FloorFacts(floor_min, floor_max, not_first, last, not_last), sorted(spans)
