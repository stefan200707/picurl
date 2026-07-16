import re
from typing import NamedTuple

from .core import Span, _iter_free, _normalize


class TimeFacts(NamedTuple):
    """Время до метро."""
    time_on_foot: int | None = None
    time_on_transport: int | None = None

_PREP = r"(?:в|до|от|за|не\s+более|менее|на)"
_MINUTES = r"(?:минут\w*|мин\w*)"
_METRO = r"(?:метро|станци\w*)"
_TRANSPORT = r"(?:транспорт\w*|машин\w*|авто|автомобил\w*)"
_FOOT = r"(?:пешком|шаг\w*)"

_T_TRANS_1 = re.compile(
    rf"\b(?:{_PREP}\s+)?(\d+)(?:\s+{_MINUTES})?(?:\s+{_PREP})?(?:\s+{_METRO})?(?:\s+{_PREP})?\s+"
    rf"{_TRANSPORT}\b"
)
_T_TRANS_2 = re.compile(
    rf"\b(?:{_PREP}\s+)?{_METRO}(?:\s+{_PREP})?\s+(\d+)(?:\s+{_MINUTES})?(?:\s+{_PREP})?\s+"
    rf"{_TRANSPORT}\b"
)

_T_FOOT_1 = re.compile(rf"\b(?:{_PREP}\s+)?(\d+)(?:\s+{_MINUTES})?(?:\s+{_PREP})?\s+{_METRO}\b")
_T_FOOT_2 = re.compile(
    rf"\b(?:{_PREP}\s+)?{_METRO}(?:\s+{_PREP})?\s+(\d+)(?:\s+{_MINUTES})?(?:\s+{_FOOT})?\b"
)
_T_FOOT_3 = re.compile(rf"\b(?:{_PREP}\s+)?(\d+)(?:\s+{_MINUTES})?\s+{_FOOT}\b")


def extract_time_to_metro(text: str) -> tuple[TimeFacts, list[Span]]:
    """Извлечь время до метро."""
    norm = _normalize(text)
    spans: list[Span] = []
    time_on_foot: int | None = None
    time_on_transport: int | None = None

    # Транспорт
    for pattern in (_T_TRANS_1, _T_TRANS_2):
        for match in _iter_free(pattern, norm, spans):
            if time_on_transport is None:
                time_on_transport = int(match.group(1))
            spans.append(match.span())

    # Пешком
    for pattern in (_T_FOOT_1, _T_FOOT_2, _T_FOOT_3):
        for match in _iter_free(pattern, norm, spans):
            if time_on_foot is None:
                time_on_foot = int(match.group(1))
            spans.append(match.span())

    return TimeFacts(time_on_foot, time_on_transport), sorted(spans)
