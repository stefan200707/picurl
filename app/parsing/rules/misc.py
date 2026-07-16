from datetime import datetime

import re

from app.parsing.schema import HousingType, Sort

from .core import Span, _iter_free, _normalize

# --- Год заселения ---

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


# --- Сортировка ---

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


# --- Выгодные предложения (requiredTags) ---

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


# --- Прочее: тип жилья, доступность, неподдерживаемое ---

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


