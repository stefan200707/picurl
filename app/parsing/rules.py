"""Regex-правила извлечения структурных фактов из свободного текста (промпт 04).

Каждая функция чистая (без сети и глобального состояния), принимает исходный
текст и возвращает пару ``(значение, consumed_spans)``, где ``consumed_spans`` —
список полуинтервалов ``(start, end)`` индексов **исходного** текста, которые
правило «поняло». Диапазоны нужны фасаду ``parse`` (промпт 06), чтобы вычислить
нераспознанные куски и отправить их в warnings.

Матчинг ведётся по нормализованной копии текста (lowercase + ё→е); нормализация
посимвольная и сохраняет длину строки, поэтому индексы совпадают с исходным
текстом. Все regex компилируются один раз на уровне модуля.

Поддерживаемые факты:

- **комнатность** — «студия», «однушка», «двушка», «трёшка», «2к»,
  «2-комнатную», перечисления («1-2 комнатные»), чип «3+» (и «2+» →
  ``[two, three_plus]``);
- **цена в рублях** — «до 15 млн», «от 10 до 15 млн», «за 15 миллионов»,
  «бюджет 15м», «800 тыс», «до 15 000 000 руб», голые большие числа
  (≥ 100 000 → рубли);
- **площадь** — общая («от 70 м²», «70-100 метров», «площадь от 40») и кухня
  («кухня от 8», «с кухней от 10 метров»); различаются по ключевым словам;
- **этаж** — «с 5 по 20 этаж» → ``floor_min/max``; «не первый», «высокий этаж»
  → ``not_first_floor``; «последний» → ``last_floor`` (с защитой от «не
  последний»);
- **отделка** — «с отделкой»/«чистовая» → ``finish=True``; «без
  отделки»/«черновая» → ``finish=False``;
- **заселение** — «заселение сразу», «готовый дом», «сдан» → ``ready=True``;
- **сортировка** — «подешевле»/«сначала дешевле» → ``price_asc``, «подороже» →
  ``price_desc``, «площадь побольше» → ``area_desc``, «площадь поменьше» →
  ``area_asc``;
- **прочее** — «только квартиры»/«без апартаментов» →
  ``housing_type=flats_only``; «не бронь»/«доступные» → ``only_available``;
- **неподдерживаемое** — «вторичка» (pik.ru — только новостройки, открытый
  вопрос №3): критерии не трогаются, фрагмент возвращается как маркер для
  warning.

Агрегат :func:`apply_rules` прогоняет все правила и собирает готовый
:class:`~app.parsing.schema.Criteria` + объединённые диапазоны + маркеры.
"""

import re
from collections.abc import Iterable, Iterator
from typing import NamedTuple

from app.parsing.schema import Criteria, HousingType, Rooms, Sort

#: Полуинтервал [start, end) индексов исходного текста, «съеденный» правилом.
Span = tuple[int, int]

# ---------------------------------------------------------------------------
# Нормализация и общие помощники
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Нормализовать текст для матчинга, сохранив длину (индексы не съезжают).

    Посимвольно: lowercase (если однобуквенный результат) + ё→е. Пробелы не
    схлопываются — regex-паттерны сами используют ``\\s+``.
    """
    out: list[str] = []
    for char in text:
        low = char.lower()
        if len(low) != 1:  # экзотика вроде İ → i̇ ломает индексы; не трогаем
            low = char
        out.append("е" if low == "ё" else low)
    return "".join(out)


def _overlaps(span: Span, spans: Iterable[Span]) -> bool:
    """Пересекается ли ``span`` хотя бы с одним из ``spans``."""
    start, end = span
    return any(start < other_end and other_start < end for other_start, other_end in spans)


def _iter_free(
    pattern: re.Pattern[str], text: str, consumed: Iterable[Span]
) -> Iterator[re.Match[str]]:
    """Матчи ``pattern``, не пересекающиеся с уже «съеденными» диапазонами."""
    taken = list(consumed)
    for match in pattern.finditer(text):
        if not _overlaps(match.span(), taken):
            yield match


#: Число: «15», «15,5», «9.5», «15 000 000» (пробел/неразрывный/узкий).
_NUM = r"\d{1,3}(?:[   ]\d{3})+|\d+(?:[.,]\d+)?"


def _to_number(raw: str) -> float:
    """Разобрать число из текста: разделители тысяч, запятая как точка."""
    cleaned = raw.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
    return float(cleaned)


# ---------------------------------------------------------------------------
# Комнатность
# ---------------------------------------------------------------------------

_ROOMS_WORD_PATTERNS: list[tuple[re.Pattern[str], Rooms]] = [
    (re.compile(r"\bстуди\w*"), Rooms.STUDIO),
    (re.compile(r"\bоднушк\w*|\bоднокомнатн\w*"), Rooms.ONE),
    (re.compile(r"\bдвушк\w*|\bдвухкомнатн\w*"), Rooms.TWO),
    (
        re.compile(r"\bтрешк\w*|\bтрехкомнатн\w*|\bчетырехкомнатн\w*|\bмногокомнатн\w*"),
        Rooms.THREE_PLUS,
    ),
]

#: «2к», «1-комнатную», «2-х комнатная», «1-2 комнатные», «1, 2 и 3 комнатные».
_ROOMS_NUM = re.compile(
    r"\b(\d(?:\s*[-–—,/]\s*\d|\s+и(?:ли)?\s+\d)*)"  # 1: цифра или перечисление цифр
    r"(?:\s*\+)?"  # хвостовой «+» («3+ комнаты»)
    r"(?:\s*[-–—]?\s*х)?"  # «2-х», «3х»
    r"\s*[-–—]?\s*"
    r"(?:комнат\w*|комн\.|к\b)"
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

    for pattern, room in _ROOMS_WORD_PATTERNS:
        for match in pattern.finditer(norm):
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


# ---------------------------------------------------------------------------
# Цена
# ---------------------------------------------------------------------------


class PriceFacts(NamedTuple):
    """Ценовые границы в рублях (None = не задано)."""

    price_min: int | None = None
    price_max: int | None = None


_PRICE_UNIT = r"млн\.?|миллион\w*|тыс\w*|руб\w*|р\.|₽"

#: «10-15 млн», «от 10 до 15 млн»; без единицы — только большие числа (рубли).
_PRICE_RANGE = re.compile(
    rf"\b(?:от\s+)?({_NUM})\s*(?:[-–—]|до)\s*({_NUM})\s*({_PRICE_UNIT})?(?![\w²])"
)
#: «бюджет 15м», «цена до 15 млн», «бюджет 15» (число <1000 → миллионы).
_PRICE_BUDGET = re.compile(
    rf"\b(?:бюджет\w*|цена|стоимость\w*)\s*[—:\-]?\s*(до|от)?\s*({_NUM})\s*"
    rf"({_PRICE_UNIT}|м|m|к|k)?(?![\w²])"
)
_PRICE_MIN = re.compile(rf"\b(?:от|не\s+дешевле|минимум)\s+({_NUM})\s*({_PRICE_UNIT})(?![\w²])")
_PRICE_MAX = re.compile(
    rf"\b(?:до|не\s+дороже|не\s+больше|максимум|в\s+пределах)\s+({_NUM})\s*"
    rf"({_PRICE_UNIT})(?![\w²])"
)
#: «за 15 миллионов», «за 15 млн» — трактуем как верхнюю границу.
_PRICE_ZA = re.compile(rf"\bза\s+({_NUM})\s*(млн\.?|миллион\w*|тыс\w*)(?![\w²])")
#: Слитный суффикс: «до 15м», «за 800к» (м/m → млн, к/k → тыс, только слитно).
_PRICE_SUFFIX = re.compile(rf"\b(до|от|за)\s+({_NUM})([мmкk])\b")
#: Голое большое число: «до 15000000» (≥ 100 000 → рубли).
_PRICE_PLAIN_MAX = re.compile(rf"\b(?:до|не\s+дороже)\s+({_NUM})\b")
_PRICE_PLAIN_MIN = re.compile(rf"\b(?:от|не\s+дешевле)\s+({_NUM})\b")

#: Порог «голое число — это рубли» (иначе слишком похоже на этаж/площадь).
_RUBLE_THRESHOLD = 100_000


def _price_multiplier(unit: str) -> int:
    """Множитель денежной единицы: млн/м → 1e6, тыс/к → 1e3, руб → 1."""
    u = unit.strip().rstrip(".")
    if u in ("м", "m") or u.startswith(("млн", "миллион")):
        return 1_000_000
    if u in ("к", "k") or u.startswith("тыс"):
        return 1_000
    return 1


def extract_price(text: str) -> tuple[PriceFacts, list[Span]]:
    """Извлечь границы цены, нормализовав в рубли (15 млн → 15_000_000)."""
    norm = _normalize(text)
    spans: list[Span] = []
    price_min: int | None = None
    price_max: int | None = None

    for match in _iter_free(_PRICE_RANGE, norm, spans):
        low, high = _to_number(match.group(1)), _to_number(match.group(2))
        unit = match.group(3)
        if unit is None:
            if low < _RUBLE_THRESHOLD or high < _RUBLE_THRESHOLD:
                continue  # «70-100 метров», «5-20 этаж» — не цена
            mult = 1
        else:
            mult = _price_multiplier(unit)
        if price_min is None:
            price_min = round(low * mult)
        if price_max is None:
            price_max = round(high * mult)
        spans.append(match.span())

    for match in _iter_free(_PRICE_BUDGET, norm, spans):
        value = _to_number(match.group(2))
        unit = match.group(3)
        if unit is None:
            # Эвристика: «бюджет 15» → миллионы; «бюджет 15000000» → рубли.
            if value < 1_000:
                mult = 1_000_000
            elif value >= _RUBLE_THRESHOLD:
                mult = 1
            else:
                continue
        else:
            if unit in ("к", "k") and value < 100:
                continue  # «2к» — комнатность, не «2 тысячи»
            mult = _price_multiplier(unit)
        rubles = round(value * mult)
        if match.group(1) == "от":
            if price_min is None:
                price_min = rubles
                spans.append(match.span())
        elif price_max is None:
            price_max = rubles
            spans.append(match.span())

    for match in _iter_free(_PRICE_MIN, norm, spans):
        if price_min is None:
            price_min = round(_to_number(match.group(1)) * _price_multiplier(match.group(2)))
            spans.append(match.span())

    for match in _iter_free(_PRICE_MAX, norm, spans):
        if price_max is None:
            price_max = round(_to_number(match.group(1)) * _price_multiplier(match.group(2)))
            spans.append(match.span())

    for match in _iter_free(_PRICE_ZA, norm, spans):
        if price_max is None:
            price_max = round(_to_number(match.group(1)) * _price_multiplier(match.group(2)))
            spans.append(match.span())

    for match in _iter_free(_PRICE_SUFFIX, norm, spans):
        value = _to_number(match.group(2))
        unit = match.group(3)
        if unit in ("к", "k") and value < 100:
            continue
        rubles = round(value * _price_multiplier(unit))
        if match.group(1) == "от":
            if price_min is None:
                price_min = rubles
                spans.append(match.span())
        elif price_max is None:
            price_max = rubles
            spans.append(match.span())

    for pattern, is_min in ((_PRICE_PLAIN_MIN, True), (_PRICE_PLAIN_MAX, False)):
        for match in _iter_free(pattern, norm, spans):
            value = _to_number(match.group(1))
            if value < _RUBLE_THRESHOLD:
                continue
            if is_min and price_min is None:
                price_min = round(value)
                spans.append(match.span())
            elif not is_min and price_max is None:
                price_max = round(value)
                spans.append(match.span())

    return PriceFacts(price_min, price_max), sorted(spans)


# ---------------------------------------------------------------------------
# Площадь (общая и кухни)
# ---------------------------------------------------------------------------


class AreaFacts(NamedTuple):
    """Границы площади в м² (None = не задано)."""

    area_min: float | None = None
    area_max: float | None = None
    area_kitchen_min: float | None = None
    area_kitchen_max: float | None = None


_AREA_UNIT = r"(?:м²|м2|кв\.?\s*метр\w*|кв\.?\s*м\.?|квадрат\w*|метр\w*)"

#: «кухня от 8», «с кухней от 10 метров», «кухня 8-12», «кухня 10 м²».
_KITCHEN = re.compile(
    rf"(?:с\s+)?\bкухн\w*\s*[—:\-]?\s*"
    rf"(?:"
    rf"(?:от|не\s+меньше|минимум)\s+({_NUM})"  # 1: min
    rf"|(?:до|не\s+больше|максимум)\s+({_NUM})"  # 2: max
    rf"|({_NUM})\s*[-–—]\s*({_NUM})"  # 3, 4: диапазон
    rf"|({_NUM})"  # 5: точное значение → min
    rf")"
    rf"(?:\s*{_AREA_UNIT})?"
)
#: «площадь от 40», «общей площадью 50-70» (единица опциональна).
_AREA_KEYWORD = re.compile(
    rf"(?:общ\w+\s+)?\bплощад\w*\s*[—:\-]?\s*"
    rf"(?:"
    rf"(?:от|не\s+меньше|минимум)\s+({_NUM})"  # 1: min
    rf"|(?:до|не\s+больше|максимум)\s+({_NUM})"  # 2: max
    rf"|({_NUM})\s*[-–—]\s*({_NUM})"  # 3, 4: диапазон
    rf"|({_NUM})"  # 5: точное значение → min
    rf")"
    rf"(?:\s*{_AREA_UNIT})?"
)
#: «70-100 метров», «от 40 до 60 м²» — единица обязательна.
_AREA_RANGE = re.compile(rf"\b(?:от\s+)?({_NUM})\s*(?:[-–—]|до)\s*({_NUM})\s*{_AREA_UNIT}")
_AREA_MIN = re.compile(rf"\b(?:от|не\s+меньше|минимум)\s+({_NUM})\s*{_AREA_UNIT}")
_AREA_MAX = re.compile(rf"\b(?:до|не\s+больше|максимум)\s+({_NUM})\s*{_AREA_UNIT}")


def _area_bounds(match: re.Match[str]) -> tuple[float | None, float | None]:
    """Разобрать группы _KITCHEN/_AREA_KEYWORD в пару (min, max)."""
    g_min, g_max, g_lo, g_hi, g_exact = match.group(1, 2, 3, 4, 5)
    if g_min is not None:
        return _to_number(g_min), None
    if g_max is not None:
        return None, _to_number(g_max)
    if g_lo is not None and g_hi is not None:
        return _to_number(g_lo), _to_number(g_hi)
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

    for pattern in (_AREA_KEYWORD, _AREA_RANGE, _AREA_MIN, _AREA_MAX):
        for match in _iter_free(pattern, norm, spans):
            if pattern in (_AREA_KEYWORD, _KITCHEN):
                low, high = _area_bounds(match)
            elif pattern is _AREA_RANGE:
                low, high = _to_number(match.group(1)), _to_number(match.group(2))
            elif pattern is _AREA_MIN:
                low, high = _to_number(match.group(1)), None
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


# ---------------------------------------------------------------------------
# Время до метро
# ---------------------------------------------------------------------------


class TimeFacts(NamedTuple):
    """Время до метро."""

    time_on_foot: int | None = None
    time_on_transport: int | None = None


_TIME_ON_FOOT = re.compile(
    r"\b(?:до|не\s+более|менее)\s+(\d+)\s*(?:мин\w*)?\s*до\s*метро\b|"
    r"\bдо\s+метро\s+(?:до|не\s+более|менее)\s+(\d+)\s*(?:мин\w*)?\b"
)
_TIME_ON_TRANSPORT = re.compile(
    r"\b(?:до|не\s+более|менее)\s+(\d+)\s*(?:мин\w*)?\s*(?:на\s+транспорте|транспортом)\b|"
    r"\bдо\s+метро\s+(?:до|не\s+более|менее)\s+(\d+)\s*(?:мин\w*)?\s*(?:на\s+транспорте|транспортом)\b"
)


def extract_time_to_metro(text: str) -> tuple[TimeFacts, list[Span]]:
    """Извлечь время до метро."""
    norm = _normalize(text)
    spans: list[Span] = []
    time_on_foot: int | None = None
    time_on_transport: int | None = None

    for match in _iter_free(_TIME_ON_FOOT, norm, spans):
        if time_on_foot is None:
            time_on_foot = int(match.group(1) or match.group(2))
            spans.append(match.span())

    for match in _iter_free(_TIME_ON_TRANSPORT, norm, spans):
        if time_on_transport is None:
            time_on_transport = int(match.group(1) or match.group(2))
            spans.append(match.span())

    return TimeFacts(time_on_foot, time_on_transport), sorted(spans)


# ---------------------------------------------------------------------------
# Этаж

# ---------------------------------------------------------------------------


class FloorFacts(NamedTuple):
    """Ограничения по этажу."""

    floor_min: int | None = None
    floor_max: int | None = None
    not_first_floor: bool = False
    last_floor: bool = False


_FLOOR_RANGE_A = re.compile(r"\bс\s+(\d+)\s+(?:по|до)\s+(\d+)\s*(?:-?го)?\s*этаж\w*")
_FLOOR_RANGE_B = re.compile(r"\bэтаж\w*\s*[—:\-]?\s*с\s+(\d+)\s+(?:по|до)\s+(\d+)")
_FLOOR_RANGE_C = re.compile(r"\b(\d+)\s*[-–—]\s*(\d+)\s+этаж\w*")
_FLOOR_MIN = re.compile(r"\b(?:не\s+ниже|от|начиная\s+с|с)\s+(\d+)(?:-?го)?\s+этаж\w*")
_FLOOR_MAX = re.compile(r"\b(?:не\s+выше|до)\s+(\d+)(?:-?го)?\s+этаж\w*")
_FLOOR_NOT_FIRST = re.compile(r"\b(?:не\s+(?:на\s+)?|кроме\s+|выше\s+)перв\w+(?:\s+этаж\w*)?")
_FLOOR_HIGH = re.compile(r"\bвысок\w+\s+этаж\w*|\bэтаж\w*\s+повыше|\bповыше\s+этаж\w*")
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

    for pattern in (_FLOOR_RANGE_A, _FLOOR_RANGE_B, _FLOOR_RANGE_C):
        for match in _iter_free(pattern, norm, spans):
            if floor_min is None:
                floor_min = int(match.group(1))
            if floor_max is None:
                floor_max = int(match.group(2))
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

    for match in _iter_free(_FLOOR_LAST, norm, spans):
        if _NEGATION_BEFORE.search(norm[: match.start()]):
            continue  # «не последний этаж» — в URL не выражается, уйдёт в warnings
        last = True
        spans.append(match.span())

    return FloorFacts(floor_min, floor_max, not_first, last), sorted(spans)


# ---------------------------------------------------------------------------
# Отделка и заселение
# ---------------------------------------------------------------------------

_FINISH_FALSE = re.compile(r"\bбез\s+(?:отделки|ремонта)\b|\bчернов\w+(?:\s+отделк\w+)?")
_FINISH_TRUE = re.compile(
    r"\bс\s+(?:отделкой|ремонтом)\b|\b(?:пред)?чистов\w+(?:\s+отделк\w+)?"
    r"|\bготов\w+\s+отделк\w+|\bпод\s+ключ\b"
)


def extract_finish(text: str) -> tuple[bool | None, list[Span]]:
    """Извлечь отделку: «с отделкой» → True, «без отделки»/«черновая» → False.

    При противоречивых упоминаниях побеждает первое по тексту; все найденные
    диапазоны при этом считаются «съеденными».
    """
    norm = _normalize(text)
    candidates: list[tuple[int, bool, Span]] = []
    for match in _FINISH_FALSE.finditer(norm):
        candidates.append((match.start(), False, match.span()))
    for match in _FINISH_TRUE.finditer(norm):
        candidates.append((match.start(), True, match.span()))
    if not candidates:
        return None, []
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1], sorted(span for _, _, span in candidates)


_READY = re.compile(
    r"\bзаселение\s+сразу\b|\bготов\w+\s+дом\w*|\bдом\w*\s+(?:уже\s+)?готов\w*"
    r"|\bсдан\w*|\bможно\s+(?:сразу\s+)?заехать\b|\bключи\s+сразу\b"
)


def extract_ready(text: str) -> tuple[bool | None, list[Span]]:
    """Извлечь готовность к заселению: «заселение сразу», «готовый дом», «сдан»."""
    norm = _normalize(text)
    spans = [match.span() for match in _READY.finditer(norm)]
    return (True, spans) if spans else (None, [])


# ---------------------------------------------------------------------------
# Сортировка
# ---------------------------------------------------------------------------

_SORT_PATTERNS: list[tuple[re.Pattern[str], Sort]] = [
    (
        re.compile(
            r"\bподешевле\b|\bсначала\s+(?:по)?дешев\w+|\bдешевле\s+сначала\b"
            r"|\bсначала\s+недорог\w+|\bпо\s+возрастанию\s+цены\b"
            r"|\bот\s+дешев\w+\s+к\s+дорог\w+"
        ),
        Sort.PRICE_ASC,
    ),
    (
        re.compile(
            r"\bподороже\b|\bсначала\s+(?:по)?дорог\w+|\bдороже\s+сначала\b"
            r"|\bпо\s+убыванию\s+цены\b"
        ),
        Sort.PRICE_DESC,
    ),
    (
        re.compile(
            r"\bплощад\w*\s+побольше\b|\bпобольше\s+площад\w*|\bпо\s+убыванию\s+площади\b"
            r"|\bсначала\s+(?:самые\s+)?просторн\w+|\bсначала\s+(?:самые\s+)?больш\w+"
        ),
        Sort.AREA_DESC,
    ),
    (
        re.compile(
            r"\bплощад\w*\s+поменьше\b|\bпоменьше\s+площад\w*"
            r"|\bпо\s+возрастанию\s+площади\b|\bсначала\s+(?:самые\s+)?маленьк\w+"
            r"|\bсначала\s+небольш\w+"
        ),
        Sort.AREA_ASC,
    ),
]


def extract_sort(text: str) -> tuple[Sort | None, list[Span]]:
    """Извлечь сортировку («подешевле» → price_asc и т.п.); первый матч побеждает."""
    norm = _normalize(text)
    for pattern, sort in _SORT_PATTERNS:
        match = pattern.search(norm)
        if match:
            return sort, [match.span()]
    return None, []


# ---------------------------------------------------------------------------
# Прочее: тип жилья, доступность, неподдерживаемое
# ---------------------------------------------------------------------------

_FLATS_ONLY = re.compile(r"\bтолько\s+квартир\w*|\bбез\s+апартаментов\b|\bне\s+апартамент\w*")


def extract_housing_type(text: str) -> tuple[HousingType | None, list[Span]]:
    """«Только квартиры» / «без апартаментов» → ``HousingType.FLATS_ONLY``."""
    norm = _normalize(text)
    spans = [match.span() for match in _FLATS_ONLY.finditer(norm)]
    return (HousingType.FLATS_ONLY, spans) if spans else (None, [])


_ONLY_AVAILABLE = re.compile(
    r"\bне\s+бронь\b|\bбез\s+брони\b|\bне\s+забронированн\w+"
    r"|\bтолько\s+свободн\w+|\bтолько\s+доступн\w+|\bдоступн\w+"
)


def extract_only_available(text: str) -> tuple[bool, list[Span]]:
    """«Не бронь» / «только свободные» / «доступные» → ``only_available=True``."""
    norm = _normalize(text)
    spans = [match.span() for match in _ONLY_AVAILABLE.finditer(norm)]
    return bool(spans), spans


#: «Вторичка» — pik.ru продаёт только новостройки (открытый вопрос №3):
#: критерии не трогаем, фрагмент уходит маркером для warning.
_UNSUPPORTED = re.compile(
    r"\bвторичк\w*|\bвторичн\w+(?:\s+(?:рынок|рынке|рынка|жиль\w*|фонд\w*))?"
    r"|\bсолнечн\w+\s+сторон\w*|\bраздельн\w+\s+сануз\w*"
    r"|\bбалкон\w*|\bлоджи\w*|\bпанорамн\w+\s+окн\w*"
    r"|\bокн\w+\s+во\s+двор\b|\bкирпичн\w+\s+дом\w*"
    r"|\bвид\w*\s+на\s+парк\b|\bс\s+тепл\w+\s+пол\w*\b"
    r"|\bтепл\w+\s+пол\w*|\b(?:два|несколько)\s+(?:и\s+более\s+)?санузл\w*"
)


def extract_unsupported(text: str) -> tuple[list[tuple[str, str]], list[Span]]:
    """Найти неподдерживаемые пожелания («вторичка»): фрагменты для warnings."""
    norm = _normalize(text)
    fragments: list[tuple[str, str]] = []
    spans: list[Span] = []
    for match in _UNSUPPORTED.finditer(norm):
        fragments.append((text[match.start() : match.end()], "фильтр не поддерживается сайтом ПИК"))
        spans.append(match.span())
    return fragments, spans


_FALLBACK_METRO = re.compile(r"(?i)\bу\s+метро\s+([а-яА-ЯёЁ-]+)")


def extract_fallback_metro(text: str) -> tuple[list[str], list[Span]]:
    """Извлечь гео-маркеры, которые могли не попасть в словарь."""
    fragments: list[str] = []
    spans: list[Span] = []
    for match in _FALLBACK_METRO.finditer(text):
        fragments.append(match.group(1))
        spans.append(match.span())
    return fragments, spans


# ---------------------------------------------------------------------------
# Агрегат: все правила разом
# ---------------------------------------------------------------------------


class RulesOutcome(NamedTuple):
    """Результат прогона всех правил по тексту."""

    criteria: Criteria
    consumed: list[Span]
    unsupported: list[tuple[str, str]]
    fallback_metro: list[tuple[str, Span]]


def apply_rules(text: str) -> RulesOutcome:
    """Прогнать все правила и собрать структурные факты в ``Criteria``.

    Возвращает критерии, объединённые «съеденные» диапазоны (для вычисления
    нераспознанных кусков в фасаде ``parse``, промпт 06) и маркеры
    неподдерживаемых пожеланий («вторичка») для warnings.
    """
    rooms, rooms_spans = extract_rooms(text)
    price, price_spans = extract_price(text)
    area, area_spans = extract_area(text)
    time_metro, time_metro_spans = extract_time_to_metro(text)
    floor, floor_spans = extract_floor(text)
    finish, finish_spans = extract_finish(text)
    ready, ready_spans = extract_ready(text)
    sort, sort_spans = extract_sort(text)
    housing_type, housing_spans = extract_housing_type(text)
    only_available, available_spans = extract_only_available(text)
    unsupported, unsupported_spans = extract_unsupported(text)
    fallback_names, fallback_spans = extract_fallback_metro(text)
    fallback_metro = list(zip(fallback_names, fallback_spans, strict=False))

    criteria = Criteria(
        rooms=rooms,
        price_min=price.price_min,
        price_max=price.price_max,
        area_min=area.area_min,
        area_max=area.area_max,
        area_kitchen_min=area.area_kitchen_min,
        area_kitchen_max=area.area_kitchen_max,
        time_on_foot=time_metro.time_on_foot,
        time_on_transport=time_metro.time_on_transport,
        floor_min=floor.floor_min,
        floor_max=floor.floor_max,
        not_first_floor=floor.not_first_floor,
        last_floor=floor.last_floor,
        finish=finish,
        ready=ready,
        sort=sort,
        housing_type=housing_type,
        only_available=only_available,
    )
    consumed = sorted(
        [
            *rooms_spans,
            *price_spans,
            *area_spans,
            *time_metro_spans,
            *floor_spans,
            *finish_spans,
            *ready_spans,
            *sort_spans,
            *housing_spans,
            *available_spans,
            *unsupported_spans,
        ]
    )
    return RulesOutcome(criteria, consumed, unsupported, fallback_metro)
