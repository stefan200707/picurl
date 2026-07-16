import re

from app.parsing.schema import Rooms

from .core import Span, _iter_free, _normalize

# --- Комнатность ---

_ROOMS_WORD_PATTERNS: list[tuple[re.Pattern[str], Rooms]] = [
    (re.compile(r"\bстуди\w*"), Rooms.STUDIO),
    (re.compile(r"\bоднушк\w*|\bоднокомнатн\w*"), Rooms.ONE),
    (re.compile(r"\bдвушк\w*|\bдвухкомнатн\w*"), Rooms.TWO),
    (
        re.compile(r"\bтрешк\w*|\bтрехкомнатн\w*|\bчетырехкомнатн\w*|\bмногокомнатн\w*"),
        Rooms.THREE_PLUS,
    ),
]

#: Отрицания комнатности, например "кроме студии" или "точно не двушку"
_ROOMS_NEGATION = re.compile(
    r"\b(?:не|кроме|без|точно\s+не)\s+"
    r"(?:студи\w*|однушк\w*|однокомнатн\w*|двушк\w*|двухкомнатн\w*|трешк\w*|трехкомнатн\w*|четырехкомнатн\w*|многокомнатн\w*"
    r"|\d\s*[-–—]?\s*(?:комнат\w*|комн\.|к\b))"
)

#: «2к», «1-комнатную», «2-х комнатная», «1-2 комнатные», «1, 2 и 3 комнатные», «даже 4-комнатную».
_ROOMS_NUM = re.compile(
    r"(?:\bдаже\s+)?"  # учитываем слово «даже» (из задания 2)
    r"\b(\d(?:\s*[-–—,/]\s*\d|\s+и(?:ли)?\s+\d)*)"
    r"(?:\s*\+)?"
    r"(?:\s*[-–—]?\s*х)?"
    r"\s*[-–—]?\s*"
    r"(?:комнат\w*|комн\.|к\b)"
    r"(?:\s+кв-р\w*|\s+квартир\w*|\s+кв\b)?"
)

#: Голый чип «3+» (без слова «комнат»).
_ROOMS_PLUS = re.compile(r"\b(\d)\s*\+(?!\s*\d)")


def _digit_rooms(digit: int) -> Rooms | None:
    """Маппинг цифры в чип комнатности: 1→one, 2→two, ≥3→three_plus."""
    if digit == 1:
        return Rooms.ONE
    if digit == 2:
        return Rooms.TWO
    if digit >= 3:
        return Rooms.THREE_PLUS
    return None


def _plus_rooms(digit: int) -> list[Rooms]:
    """«N+» → все чипы от N и выше (2+ → [two, three_plus], 3+ → [three_plus])."""
    rooms = [_digit_rooms(value) for value in range(digit, 4)]
    return list(dict.fromkeys(room for room in rooms if room is not None))


_ROOMS_WORD_RANGES = re.compile(r"\bот\s+(одн\w+|двух|трех|четырех|пяти)\s+комнат\w*")


def _word_to_digit(word: str) -> int:
    if word.startswith("одн"):
        return 1
    if word == "двух":
        return 2
    if word == "трех":
        return 3
    if word in ("четырех", "пяти"):
        return 4
    return 1


def extract_rooms(text: str) -> tuple[list[Rooms], list[Span]]:
    """Извлечь комнатность: список чипов (без дубликатов, в порядке упоминания)."""
    norm = _normalize(text)
    found: list[tuple[int, int, Rooms]] = []  # (start, порядок вставки, чип)
    spans: list[Span] = []
    order = 0

    for match in _ROOMS_NEGATION.finditer(norm):
        spans.append(match.span())

    for pattern, room in _ROOMS_WORD_PATTERNS:
        for match in _iter_free(pattern, norm, spans):
            found.append((match.start(), order, room))
            spans.append(match.span())
            order += 1

    for match in _iter_free(_ROOMS_WORD_RANGES, norm, spans):
        digit = _word_to_digit(match.group(1))
        rooms = _plus_rooms(digit)
        for room in rooms:
            found.append((match.start(), order, room))
            order += 1
        spans.append(match.span())

    for match in _iter_free(_ROOMS_NUM, norm, spans):
        digits = [int(d) for d in re.findall(r"\d", match.group(1))]
        rooms = [room for d in digits if (room := _digit_rooms(d)) is not None]
        if "+" in match.group(0) and digits:
            rooms.extend(_plus_rooms(digits[-1]))
        if not rooms:
            continue
        for room in rooms:
            found.append((match.start(), order, room))
            order += 1
        spans.append(match.span())

    for match in _iter_free(_ROOMS_PLUS, norm, spans):
        rooms = _plus_rooms(int(match.group(1)))
        if not rooms:
            continue
        for room in rooms:
            found.append((match.start(), order, room))
            order += 1
        spans.append(match.span())

    found.sort(key=lambda item: (item[0], item[1]))
    unique = list(dict.fromkeys(room for _, _, room in found))
    return unique, sorted(spans)
