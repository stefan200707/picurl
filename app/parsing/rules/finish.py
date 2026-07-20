import re

from app.parsing.schema import Finish

from .core import Span, _normalize

# --- Отделка и заселение ---

_FINISH_FALSE = re.compile(
    r"\bбез\s+(?:отделки|ремонта)\b|\bчернов\w+(?:\s+отделк\w+)?|\bотделк\w*\s+чернов\w+"
)
_FINISH_PRED = re.compile(r"\bпредчистов\w+(?:\s+отделк\w+)?|\bотделк\w*\s+предчистов\w+")
_FINISH_TRUE = re.compile(
    r"\bс\s+(?:отделкой|ремонтом)\b|\bчистов\w+(?:\s+отделк\w+)?|\bотделк\w*\s+чистов\w+"
    r"|\bготов\w+\s+отделк\w+|\bпод\s+ключ\b"
)


_FINISH_FURNISHED = re.compile(r"\b(?:готов\w+\s+)?отделк\w+\s+с\s+мебелью\b|\bс\s+мебель\w+")


def extract_finish(text: str) -> tuple[list[Finish], list[Span]]:
    """Извлечь отделку (список значений Finish).

    При противоречивых упоминаниях сохраняются все уникальные запрошенные варианты.
    """
    norm = _normalize(text)
    candidates: list[tuple[int, Finish, Span]] = []

    for match in _FINISH_FALSE.finditer(norm):
        candidates.append((match.start(), Finish.NONE, match.span()))
    for match in _FINISH_PRED.finditer(norm):
        candidates.append((match.start(), Finish.WHITE_BOX, match.span()))
    for match in _FINISH_FURNISHED.finditer(norm):
        candidates.append((match.start(), Finish.FURNISHED, match.span()))
    for match in _FINISH_TRUE.finditer(norm):
        is_inside_false_or_furnished = False
        for c in candidates:
            if (
                c[1] in (Finish.NONE, Finish.FURNISHED)
                and c[2][0] <= match.start()
                and c[2][1] >= match.end()
            ):
                is_inside_false_or_furnished = True
                break
        if not is_inside_false_or_furnished:
            candidates.append((match.start(), Finish.READY, match.span()))

    if not candidates:
        return [], []

    # Сортируем по старту, при равном старте предпочтение более длинному матчу
    candidates.sort(key=lambda item: (item[0], item[2][1] - item[2][0]))

    # Удаляем полностью поглощенные
    filtered = []
    for c in candidates:
        if not filtered:
            filtered.append(c)
        else:
            prev = filtered[-1]
            if prev[2][0] <= c[2][0] and prev[2][1] >= c[2][1]:
                continue  # c is completely inside prev, ignore
            if c[2][0] <= prev[2][0] and c[2][1] >= prev[2][1]:
                filtered[-1] = c  # prev is completely inside c, replace
            else:
                filtered.append(c)

    unique_finishes = []
    seen = set()
    for _, finish, _ in filtered:
        if finish not in seen:
            unique_finishes.append(finish)
            seen.add(finish)

    return unique_finishes, sorted(span for _, _, span in filtered)


_READY = re.compile(
    r"\bзаселение\s+сразу\b|\bготов\w+\s+дом\w*|\bдом\w*\s+(?:уже\s+)?готов\w*"
    r"|\bсдан\w*|\bможно\s+(?:сразу\s+)?заехать\b|\bключи\s+сразу\b"
)


def extract_ready(text: str) -> tuple[bool | None, list[Span]]:
    """Извлечь готовность к заселению: «заселение сразу», «готовый дом», «сдан»."""
    norm = _normalize(text)
    spans = [match.span() for match in _READY.finditer(norm)]
    return (True, spans) if spans else (None, [])
