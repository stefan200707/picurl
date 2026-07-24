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
_FOOT = r"(?:пешком|пешк\w*|шаг\w*|ходьб\w*)"
#: Идиома «(в пешей/шаговой) доступности» — «в шаговой доступности», «было в
#: доступности». Модель одного предлога между метро и числом (см. _T_FOOT_2) её
#: не покрывала: между «метро» и числом стоят «было в доступности».
_ACCESS = r"(?:пеш\w+\s+|шагов\w+\s+)?доступност\w+"

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
#: «метро … (было в) (пешей/шаговой) доступности N минут» — до 3 незначимых слов
#: между метро и идиомой доступности.
_T_FOOT_ACCESS = re.compile(
    rf"\b{_METRO}(?:\s+\w+){{0,3}}?\s+{_ACCESS}\s+(?:{_PREP}\s+)?(\d+)\s*{_MINUTES}"
)
#: «в шаговой доступности 7 минут» без явного слова «метро» — доступность к
#: метро подразумевается; дефолт — пеший.
_T_FOOT_ACCESS2 = re.compile(rf"\b{_ACCESS}\s+(?:{_PREP}\s+)?(\d+)\s*{_MINUTES}")


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

    # Пешком (идиомы доступности — раньше явных, длиннее матч → точнее спан)
    for pattern in (_T_FOOT_ACCESS, _T_FOOT_ACCESS2, _T_FOOT_1, _T_FOOT_2, _T_FOOT_3):
        for match in _iter_free(pattern, norm, spans):
            if time_on_foot is None:
                time_on_foot = int(match.group(1))
            spans.append(match.span())

    return TimeFacts(time_on_foot, time_on_transport), sorted(spans)
