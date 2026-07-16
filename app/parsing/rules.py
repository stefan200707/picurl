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
from datetime import datetime
from typing import NamedTuple

from yargy import Parser, or_, rule
from yargy.interpretation import fact
from yargy.pipelines import morph_pipeline
from yargy.predicates import dictionary
from yargy.predicates import type as yargy_type

from app.parsing.schema import Criteria, Finish, HousingType, Rooms, Sort

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


# ---------------------------------------------------------------------------
# Цена
# ---------------------------------------------------------------------------


class PriceFacts(NamedTuple):
    """Ценовые границы в рублях (None = не задано)."""

    price_min: int | None = None
    price_max: int | None = None


#: «лям/ляма/лямов» — разговорный синоним «миллион» («12 лямов», «бабок 12 лямов»).
_PRICE_UNIT = r"млн\.?|миллион\w*|лям\w*|тыс\w*|руб\w*|р\.|₽"
_OPT_RUB = r"(?:\s+(?:рублей|руб\w*|р\.|₽))?"  # опциональный суффикс рублей

#: «10-15 млн», «от 10 до 15 млн»; без единицы — только большие числа (рубли).
_PRICE_RANGE = re.compile(
    rf"\b(?:(?:бюджет\w*|цена|стоимость\w*)\s*[—:\-]?\s*)?(?:от\s+)?({_NUM})\s*({_PRICE_UNIT}|[мmкk])?\s*(?:[-–—]|до)\s*({_NUM})\s*({_PRICE_UNIT}|[мmкk])?{_OPT_RUB}(?![\w²])"
)
#: «бюджет 15м», «цена до 15 млн», «бюджет 15», «бабок 12 лямов» (число <1000 → миллионы).
_PRICE_BUDGET = re.compile(
    rf"\b(?:бюджет\w*|цена|стоимость\w*|баб\w+)\s*[—:\-]?\s*(не\s+более|до|от)?\s*({_NUM})\s*"
    rf"({_PRICE_UNIT}|м|m|к|k)?{_OPT_RUB}(?![\w²])"
)
_PRICE_MIN = re.compile(
    rf"\b(?:не\s+менее|от|не\s+дешевле|минимум)\s+({_NUM})\s*({_PRICE_UNIT}){_OPT_RUB}(?![\w²])"
)
_PRICE_MAX = re.compile(
    rf"\b(?:не\s+более|до|не\s+дороже|не\s+больше|максимум|в\s+пределах)\s+({_NUM})\s*"
    rf"({_PRICE_UNIT}){_OPT_RUB}(?![\w²])"
)
#: «за 15 миллионов», «за 15 млн» — трактуем как верхнюю границу.
_PRICE_ZA = re.compile(rf"\bза\s+({_NUM})\s*({_PRICE_UNIT}){_OPT_RUB}(?![\w²])")
#: Слитный суффикс: «до 15м», «за 800к» (м/m → млн, к/k → тыс, только слитно).
_PRICE_SUFFIX = re.compile(rf"\b(до|от|за)\s+({_NUM})([мmкk]){_OPT_RUB}\b")
#: Голое большое число: «до 15000000» (≥ 100 000 → рубли).
_PRICE_PLAIN_MAX = re.compile(rf"\b(?:до|не\s+дороже)\s+({_NUM}){_OPT_RUB}\b")
_PRICE_PLAIN_MIN = re.compile(rf"\b(?:от|не\s+дешевле)\s+({_NUM}){_OPT_RUB}\b")
_PRICE_STANDALONE = re.compile(rf"\b({_NUM})\s*({_PRICE_UNIT}){_OPT_RUB}\b")

#: Порог «голое число — это рубли» (иначе слишком похоже на этаж/площадь).
_RUBLE_THRESHOLD = 100_000


def _price_multiplier(unit: str) -> int:
    """Множитель денежной единицы: млн/м/лям → 1e6, тыс/к → 1e3, руб → 1."""
    u = unit.strip().rstrip(".")
    if u in ("м", "m") or u.startswith(("млн", "миллион", "лям")):
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

    def _update_min(value: int) -> None:
        nonlocal price_min
        price_min = min(price_min, value) if price_min is not None else value

    def _update_max(value: int) -> None:
        nonlocal price_max
        price_max = max(price_max, value) if price_max is not None else value

    for match in _iter_free(_PRICE_RANGE, norm, spans):
        low, high = _to_number(match.group(1)), _to_number(match.group(3))
        unit1, unit2 = match.group(2), match.group(4)
        if unit1 is None and unit2 is None:
            if low < _RUBLE_THRESHOLD or high < _RUBLE_THRESHOLD:
                continue  # «70-100 метров», «5-20 этаж» — не цена
            mult_low, mult_high = 1, 1
        else:
            mult_low = _price_multiplier(unit1) if unit1 else _price_multiplier(unit2)
            mult_high = _price_multiplier(unit2) if unit2 else _price_multiplier(unit1)
        _update_min(round(low * mult_low))
        _update_max(round(high * mult_high))
        spans.append(match.span())

    for match in _iter_free(_PRICE_BUDGET, norm, spans):
        value = _to_number(match.group(2))
        unit = match.group(3)
        if unit is None:
            # Защита от дат (например, "до 15.07")
            if re.fullmatch(r"\d{1,2}[.,]\d{2}", match.group(2)):
                continue

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
            _update_min(rubles)
            spans.append(match.span())
        else:
            _update_max(rubles)
            spans.append(match.span())

    for match in _iter_free(_PRICE_MIN, norm, spans):
        _update_min(round(_to_number(match.group(1)) * _price_multiplier(match.group(2))))
        spans.append(match.span())

    for match in _iter_free(_PRICE_MAX, norm, spans):
        _update_max(round(_to_number(match.group(1)) * _price_multiplier(match.group(2))))
        spans.append(match.span())

    for match in _iter_free(_PRICE_ZA, norm, spans):
        _update_max(round(_to_number(match.group(1)) * _price_multiplier(match.group(2))))
        spans.append(match.span())

    for match in _iter_free(_PRICE_SUFFIX, norm, spans):
        value = _to_number(match.group(2))
        unit = match.group(3)
        if unit in ("к", "k") and value < 100:
            continue
        rubles = round(value * _price_multiplier(unit))
        if match.group(1) == "от":
            _update_min(rubles)
            spans.append(match.span())
        else:
            _update_max(rubles)
            spans.append(match.span())

    for pattern, is_min in ((_PRICE_PLAIN_MIN, True), (_PRICE_PLAIN_MAX, False)):
        for match in _iter_free(pattern, norm, spans):
            value = _to_number(match.group(1))
            if value < _RUBLE_THRESHOLD:
                continue
            if is_min:
                _update_min(round(value))
                spans.append(match.span())
            else:
                _update_max(round(value))
                spans.append(match.span())

    for match in _iter_free(_PRICE_STANDALONE, norm, spans):
        value = _to_number(match.group(1))
        unit = match.group(2)
        rubles = round(value * _price_multiplier(unit))
        _update_max(rubles)
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


# ---------------------------------------------------------------------------
# Время до метро
# ---------------------------------------------------------------------------


class TimeFacts(NamedTuple):
    """Время до метро."""

    time_on_foot: int | None = None
    time_on_transport: int | None = None


_TimeFact = fact("TimeFact", ["time"])

_PREP = dictionary({"в", "до", "от", "за", "не более", "менее", "на"})
_NUMBER_TOKEN = yargy_type("INT").interpretation(_TimeFact.time.custom(int))
_MINUTES = morph_pipeline(["минута", "мин", "минут"])
_METRO = morph_pipeline(["метро", "станция"])
_TRANSPORT = morph_pipeline(["транспорт", "машина", "авто", "автомобиль", "транспортом"])
_FOOT = morph_pipeline(["пешком", "шаг"])

_TIME_ON_TRANSPORT_RULE = rule(
    or_(
        rule(
            _PREP.optional(),
            _NUMBER_TOKEN,
            _MINUTES.optional(),
            _PREP.optional(),
            _METRO.optional(),
            _PREP.optional(),
            _TRANSPORT,
        ),
        rule(
            _PREP.optional(),
            _METRO,
            _PREP.optional(),
            _NUMBER_TOKEN,
            _MINUTES.optional(),
            _PREP.optional(),
            _TRANSPORT,
        ),
    )
).interpretation(_TimeFact)

_TIME_ON_FOOT_RULE = rule(
    or_(
        rule(_PREP.optional(), _NUMBER_TOKEN, _MINUTES.optional(), _PREP.optional(), _METRO),
        rule(
            _PREP.optional(),
            _METRO,
            _PREP.optional(),
            _NUMBER_TOKEN,
            _MINUTES.optional(),
            _FOOT.optional(),
        ),
        rule(_PREP.optional(), _NUMBER_TOKEN, _MINUTES, _FOOT),
        rule(_PREP.optional(), _NUMBER_TOKEN, _MINUTES.optional(), _FOOT),
    )
).interpretation(_TimeFact)

_parser_time_foot = Parser(_TIME_ON_FOOT_RULE)
_parser_time_transport = Parser(_TIME_ON_TRANSPORT_RULE)


def extract_time_to_metro(text: str) -> tuple[TimeFacts, list[Span]]:
    """Извлечь время до метро."""
    norm = _normalize(text)
    spans: list[Span] = []
    time_on_foot: int | None = None
    time_on_transport: int | None = None

    # Транспорт
    for match in _parser_time_transport.findall(norm):
        match_span = (match.span.start, match.span.stop)
        if not _overlaps(match_span, spans):
            if time_on_transport is None:
                time_on_transport = match.fact.time
            spans.append(match_span)

    # Пешком
    for match in _parser_time_foot.findall(norm):
        match_span = (match.span.start, match.span.stop)
        if not _overlaps(match_span, spans):
            if time_on_foot is None:
                time_on_foot = match.fact.time
            spans.append(match_span)

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


# ---------------------------------------------------------------------------
# Отделка и заселение
# ---------------------------------------------------------------------------

_FINISH_FALSE = re.compile(
    r"\bбез\s+(?:отделки|ремонта)\b|\bчернов\w+(?:\s+отделк\w+)?|\bотделк\w*\s+чернов\w+"
)
_FINISH_PRED = re.compile(r"\bпредчистов\w+(?:\s+отделк\w+)?|\bотделк\w*\s+предчистов\w+")
_FINISH_TRUE = re.compile(
    r"\bс\s+(?:отделкой|ремонтом)\b|\bчистов\w+(?:\s+отделк\w+)?|\bотделк\w*\s+чистов\w+"
    r"|\bготов\w+\s+отделк\w+|\bпод\s+ключ\b|\bотделк\w+\b|\bремонт\w+\b"
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


# ---------------------------------------------------------------------------
# Год заселения
# ---------------------------------------------------------------------------

_SETTLEMENT_THIS_YEAR = re.compile(r"\b(?:заселение|сдача|въезд)\s+в\s+этом\s+году\b")
_SETTLEMENT_YEAR_RANGE = re.compile(
    r"\b(?:заселение|сдача|въезд)\s+с\s+(\d{4})\s+(?:по|до)\s+(\d{4})(?:\s+год\w*)?\b"
)
_SETTLEMENT_YEAR_EXACT = re.compile(
    r"\b(?:заселение|сдача|въезд)\s+(?:в\s+)?(\d{4})(?:\s+год\w*)?\b"
)


def extract_settlement_year(text: str) -> tuple[int | None, int | None, list[Span]]:
    """Извлечь сроки заселения (например 'в этом году', 'заселение с 2026 по 2027')."""
    norm = _normalize(text)
    spans: list[Span] = []

    for match in _iter_free(_SETTLEMENT_YEAR_RANGE, norm, spans):
        spans.append(match.span())
        return int(match.group(1)), int(match.group(2)), spans

    for match in _iter_free(_SETTLEMENT_YEAR_EXACT, norm, spans):
        spans.append(match.span())
        return int(match.group(1)), int(match.group(1)), spans

    for match in _iter_free(_SETTLEMENT_THIS_YEAR, norm, spans):
        spans.append(match.span())
        current_year = datetime.now().year
        return current_year, current_year, spans

    return None, None, []


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
# Выгодные предложения (requiredTags)
# ---------------------------------------------------------------------------

_REQUIRED_TAGS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bготов\w+\s+квартир\w+"), "zos"),
    (re.compile(r"\bипотек\w+\s+по\s+формуле\s+0[.,]?1%?"), "cashback"),
    (re.compile(r"\bспециальн\w+\s+цен\w*(?:\s+до\s+15\.07)?"), "crossed"),
    (re.compile(r"\bвыгода\s+до\s+-?15%(?:\s+до\s+15\.07)?"), "outlet"),
]


def extract_required_tags(text: str) -> tuple[list[str], list[Span]]:
    """Извлечь теги выгодных предложений (requiredTags)."""
    norm = _normalize(text)
    tags: list[str] = []
    spans: list[Span] = []

    for pattern, tag in _REQUIRED_TAGS_PATTERNS:
        for match in _iter_free(pattern, norm, spans):
            if tag not in tags:
                tags.append(tag)
            spans.append(match.span())

    return tags, sorted(spans)


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
    r"|\bне\s+показывать\s+забронирован\w+"
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
    r"|\bпанорамн\w+\s+окн\w*"
    r"|\bокн\w+\s+во\s+двор\b|\bкирпичн\w+\s+дом\w*"
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
    settle_year_from, settle_year_to, settle_spans = extract_settlement_year(text)
    sort, sort_spans = extract_sort(text)
    required_tags, tags_spans = extract_required_tags(text)
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
        not_last_floor=floor.not_last_floor,
        finish=finish,
        ready=ready,
        settlement_year_from=settle_year_from,
        settlement_year_to=settle_year_to,
        sort=sort,
        required_tags=required_tags,
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
            *settle_spans,
            *sort_spans,
            *tags_spans,
            *housing_spans,
            *available_spans,
            *unsupported_spans,
        ]
    )
    return RulesOutcome(criteria, consumed, unsupported, fallback_metro)
