import re
from datetime import datetime

from app.parsing.schema import HousingType, Sort

from .core import Span, _iter_free, _normalize

# --- Год заселения ---

_SETTLEMENT_THIS_YEAR = re.compile(r"\b(?:заселение|сдача|въезд)\s+в\s+этом\s+году\b")
_SETTLEMENT_YEAR_RANGE = re.compile(
    r"\b(?:заселение|сдача|въезд)\s+с\s+(\d{4})\s+(?:по|до)\s+(\d{4})(?:\s+год\w*)?\b"
)
_SETTLEMENT_YEAR_MAX = re.compile(
    r"\b(?:заселение|сдача|въезд)\s+(?:до|по|не\s+позднее)\s+(\d{4})(?:\s+год\w*)?\b"
)
_SETTLEMENT_YEAR_MIN = re.compile(
    r"\b(?:заселение|сдача|въезд)\s+(?:с|от|после|не\s+раннее|не\s+ранее)\s+(\d{4})(?:\s+год\w*)?\b"
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

    for match in _iter_free(_SETTLEMENT_YEAR_MAX, norm, spans):
        spans.append(match.span())
        return None, int(match.group(1)), spans

    for match in _iter_free(_SETTLEMENT_YEAR_MIN, norm, spans):
        spans.append(match.span())
        return int(match.group(1)), None, spans

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
    (re.compile(r"\bипотек\w+\s+по\s+формуле\s+\d[.,]?\d*%?"), "cashback"),
    (
        re.compile(
            r"\b(?:специальн\w+\s+цен\w*|спецпредложени\w*)"
            r"(?:\s+до\s+\d{1,2}\.\d{2}(?:\.\d{2,4})?)?"
        ),
        "crossed",
    ),
    (re.compile(r"\bвыгода\s+до\s+-?\d{1,2}%(?:\s+до\s+\d{1,2}\.\d{2}(?:\.\d{2,4})?)?"), "outlet"),
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
    r"|\bтолько\s+свободн\w+|\bтолько\s+доступн\w+"
    r"|\bне\s+показывать\s+забронирован\w+"
)


def extract_only_available(text: str) -> tuple[bool, list[Span]]:
    """«Не бронь» / «только свободные» / «доступные» → ``only_available=True``."""
    norm = _normalize(text)
    spans = [match.span() for match in _ONLY_AVAILABLE.finditer(norm)]
    return bool(spans), spans


#: Каталог заведомо неподдерживаемых pik.ru фильтров (Milestone AI-9).
#:
#: Здесь собраны пожелания, которых **нет и не может быть** в URL-схеме pik.ru
#: (`docs/pik-url-schema.md`): строить по ним нечего ни детерминированному
#: пайплайну, ни ИИ. Это не баг парсера — это потолок возможностей самого
#: сайта. Принципиально другой случай, чем «бэк не осилил разбор» (промпт 22,
#: фильтр есть — но не распознан) или «нет справочника сущностей» (промпт 23,
#: фильтр есть — но нет данных). Правильное поведение — детерминированно и явно
#: сказать в warnings, что именно сайт не умеет, а не выбросить фрагмент как
#: нераспознанный мусор и не позволить ИИ придумать замену.
#:
#: Каждая запись — ``(паттерн, человекочитаемое объяснение для warning)``.
#: Прежде чем заносить сюда что-либо, сверься со схемой, что фильтра правда нет
#: (а не просто отсутствует в справочнике). Поэтому «панорамные окна» и «окна
#: во двор» здесь НЕ упомянуты — они поддерживаются под именами «Большие окна»
#: (``bigwindows``) и «Вид во двор» (``vidVoDvor``), см. option_groups.json /
#: options.json; их место — матчинг сущностей, а не этот каталог.
_UNSUPPORTED_CATALOG: list[tuple[re.Pattern[str], str]] = [
    # «Вторичка» — pik.ru продаёт только новостройки (открытый вопрос №3).
    (
        re.compile(
            r"\bвторичк\w*|\bвторичн\w+(?:\s+(?:рынок|рынке|рынка|жиль\w*|фонд\w*))?"
        ),
        "вторичное жильё не поддерживается pik.ru (только новостройки) — пропущено",
    ),
    # Сторона света / вид на «светлую-солнечную-южную» сторону — фильтра нет
    # в схеме (эталонный кейс милстоуна: «вид на светлую сторону»).
    (
        re.compile(
            r"\b(?:солнечн\w+|светл\w+|темн\w+|"
            r"южн\w+|северн\w+|восточн\w+|западн\w+)\s+сторон\w*"
            r"|\bсторон\w+\s+света\b"
        ),
        "фильтр по стороне света не поддерживается pik.ru — пропущен",
    ),
    # «Раздельный санузел» — в схеме есть только «Два и более санузла»
    # (manybathrooms) и «Сквозной санузел» (throughbathroom), «раздельного» нет.
    (
        re.compile(r"\bраздельн\w+\s+сануз\w*"),
        "фильтр «раздельный санузел» не поддерживается pik.ru — пропущен",
    ),
    # Материал дома (кирпичный/монолитный/панельный/блочный) — в схеме pik.ru
    # нет фильтра по материалу стен.
    (
        re.compile(r"\b(?:кирпичн\w+|монолитн\w+|панельн\w+|блочн\w+)\s+дом\w*"),
        "фильтр по материалу дома не поддерживается pik.ru — пропущен",
    ),
]


def extract_unsupported(text: str) -> tuple[list[tuple[str, str]], list[Span]]:
    """Найти заведомо неподдерживаемые pik.ru пожелания: фрагменты для warnings.

    Возвращает ``(фрагменты, spans)``, где каждый фрагмент —
    ``(кусок_исходного_текста, объяснение)`` из `_UNSUPPORTED_CATALOG`.
    Критерии не трогаются — строить нечего, таких фильтров нет в схеме pik.ru.
    Результат отсортирован по позиции в тексте.
    """
    norm = _normalize(text)
    found: list[tuple[Span, tuple[str, str]]] = []
    for pattern, reason in _UNSUPPORTED_CATALOG:
        for match in pattern.finditer(norm):
            found.append((match.span(), (text[match.start() : match.end()], reason)))
    found.sort(key=lambda item: item[0])
    fragments = [fragment for _span, fragment in found]
    spans = [span for span, _fragment in found]
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
