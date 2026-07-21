import re

from app.geo.poi import POICategory
from app.parsing.rules.core import Span, _iter_free, _normalize
from app.parsing.schema import POIRequirement

PREFIX = r"(?:(?:с|со)\s+)?(?:\bнов\w+\s+)?"
SUFFIX = r"(?:\s+(?:поблизости|неподалеку|неподалёку|рядом\s+с\s+ним|близко))?"

_POI_PATTERNS: list[tuple[re.Pattern[str], POICategory]] = [
    (
        re.compile(PREFIX + r"(?<!вид на )(?<!видом на )\bшкол\w+" + SUFFIX),
        POICategory.SCHOOL,
    ),
    (
        re.compile(PREFIX + r"(?:\bсадик\w*|\bдетск\w+\s+сад\w*|\bдетсад\w*)" + SUFFIX),
        POICategory.KINDERGARTEN,
    ),
    (
        re.compile(PREFIX + r"(?:\bмагазин\w*|\bпродукт\w+|\bсупермаркет\w*)" + SUFFIX),
        POICategory.SHOP,
    ),
    (
        re.compile(PREFIX + r"(?:\bпарковк\w*|\bпаркинг\w*)" + SUFFIX),
        POICategory.PARKING,
    ),
    (
        re.compile(PREFIX + r"(?<!вид на )(?<!видом на )\b(?:лес\w*|парк\w*|зелен\w+)" + SUFFIX),
        POICategory.PARK_FOREST,
    ),
]

_CENTER_PATTERN = re.compile(
    r"\b(?:в\s+центре|к\s+центру|близко\s+к\s+центру|ближе\s+к\s+центру|"
    r"вблизи\s+центра|около\s+центра)(?:\s+москв\w*)?\b"
)


def extract_poi_requirements(text: str) -> tuple[list[POIRequirement], bool, list[Span]]:
    """Извлечь POI-потребности и требование 'в центре'."""
    norm = _normalize(text)
    poi_reqs: list[POIRequirement] = []
    spans: list[Span] = []

    # Сначала проверяем 'в центре'
    center_requested = False
    for match in _iter_free(_CENTER_PATTERN, norm, spans):
        center_requested = True
        spans.append(match.span())

    for pattern, category in _POI_PATTERNS:
        # TODO: parse distance, e.g. "в 300 метрах" using regex nearby the POI
        for match in _iter_free(pattern, norm, spans):
            phrase = text[match.start() : match.end()]
            poi_reqs.append(POIRequirement(category=category, raw_phrase=phrase))
            spans.append(match.span())

    return poi_reqs, center_requested, sorted(spans)
