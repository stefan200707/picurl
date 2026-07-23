"""Распознавание запросов «рядом с любой станцией класса» (Milestone AI-15).

Обобщение именованных ориентиров (промпт 23, ``app/parsing/rules/landmark.py``)
на КЛАСС точек: «рядом с МЦД не важно какой станции», «у любого метро». Это не
ограничение схемы pik.ru («сторона света», промпт 24) — пользователю подходит
ЛЮБАЯ станция указанного класса/линии, а не конкретная. Задачу решает тот же
детерминированный механизм сужения ``complexes`` по расстоянию (haversine), что
и ориентиры, только точка не одна: берётся минимальное расстояние до БЛИЖАЙШЕЙ
станции подходящего класса (см. :func:`app.geo.candidates.build_candidate_shortlist`).

Важно отличать от матчинга конкретной станции (:mod:`app.parsing.entity_match`):
«у метро Аэропорт» называет станцию по имени — этим правилом НЕ перехватывается
(нет ни явного класса «МЦД/МЦК», ни маркера «не важно», после «метро» сразу идёт
имя собственное). Ложных срабатываний на такие фразы это правило не даёт —
см. негативный тест ``test_station_class_no_false_positive_named_station``.

Маркер близости — тот же список форм, что и в ``landmark.py`` (переиспользуется
идея «маркер + объект»); дублирование сознательно ограничено этими двумя
небольшими модулями, обобщение в ``rules/core.py`` не делалось, чтобы не
трогать уже стабильный и покрытый тестами ``landmark.py``.
"""

import re

from app.parsing.rules.core import Span, _normalize, _overlaps
from app.parsing.schema import StationClassRequirement

_MARKER = (
    r"\b(?:"
    r"рядом\s+со|рядом\s+с|"
    r"поближе\s+ко|поближе\s+к|ближе\s+ко|ближе\s+к|"
    r"ближайш\w+\s+ко|ближайш\w+\s+к|"
    r"недалеко\s+от|неподалеку\s+от|неподалёку\s+от|"
    r"вблизи|близко\s+ко|близко\s+к|около|возле|"
    r"у|к"
    r")\s+"
)

#: Явное имя линии/класса: МЦД или МЦК, опционально с номером («МЦД-2»).
#: Нормализованный текст в нижнем регистре — токен матчится строчными буквами.
_LINE_TOKEN = r"(?:мцд|мцк)(?:-\d+)?"

#: Необязательный хвост-уточнение «не важно как(ой|ая|ое|им...) (станции/линии)»
#: сразу после явного имени линии — тоже нужно засчитать «понятым», иначе
#: «не важно какой станции» осталось бы в warnings рядом с уже распознанным
#: «МЦД» (регрессия исходного бага «рядом с МЦД не важно какой станции»).
_QUALIFIER_TAIL = r"(?:\s+не\s+важно\s+как\w*(?:\s+(?:станци\w*|линии))?)?"

_EXPLICIT_LINE_PATTERN = re.compile(
    _MARKER + r"(?:станци\w*\s+|линии\s+)?(" + _LINE_TOKEN + r")" + _QUALIFIER_TAIL
)

#: «Любая станция» без указания конкретной линии, уточнение ДО слова «метро»
#: («у любого метро», «недалеко от любой станции метро»).
_ANY_BEFORE_PATTERN = re.compile(_MARKER + r"люб\w+\s+(?:станци\w*\s+)?метро\b")

#: Тот же случай, но уточнение ПОСЛЕ слова «метро» («рядом с метро не важно
#: каким», «рядом с метро не важно какой станции»). Без явного уточнения «у
#: метро» не матчим вообще — это обычная связка перед именем конкретной
#: станции («у метро Аэропорт»), её распознаёт app.parsing.entity_match.
_ANY_AFTER_PATTERN = re.compile(
    _MARKER + r"(?:станци\w*\s+)?метро(?:\s+не\s+важно\s+как\w*(?:\s+станци\w*)?)"
)


def _canonical_line(token: str) -> str:
    """Каноническая форма распознанного класса линии («мцд-2» -> «МЦД-2»)."""
    prefix, sep, suffix = token.partition("-")
    canonical = {"мцд": "МЦД", "мцк": "МЦК"}[prefix]
    return f"{canonical}{sep}{suffix}" if suffix else canonical


def extract_station_class_requirements(
    text: str,
) -> tuple[list[StationClassRequirement], list[Span]]:
    """Извлечь требования «рядом с любой станцией класса» из свободного текста.

    Возвращает пару ``(requirements, consumed_spans)`` — по аналогии с
    :func:`app.parsing.rules.landmark.extract_landmark_requirements`.
    """
    norm = _normalize(text)
    reqs: list[StationClassRequirement] = []
    spans: list[Span] = []

    for match in _EXPLICIT_LINE_PATTERN.finditer(norm):
        span = match.span()
        if _overlaps(span, spans):
            continue
        reqs.append(
            StationClassRequirement(
                line_prefix=_canonical_line(match.group(1)),
                raw=text[span[0] : span[1]],
            )
        )
        spans.append(span)

    for pattern in (_ANY_BEFORE_PATTERN, _ANY_AFTER_PATTERN):
        for match in pattern.finditer(norm):
            span = match.span()
            if _overlaps(span, spans):
                continue
            reqs.append(StationClassRequirement(line_prefix="метро", raw=text[span[0] : span[1]]))
            spans.append(span)

    return reqs, spans
