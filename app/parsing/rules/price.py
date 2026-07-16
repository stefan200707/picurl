
import re
from typing import NamedTuple

from .core import _NUM, Span, _iter_free, _normalize, _to_number

# --- Цена ---


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


