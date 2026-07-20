import re
from app.geo.poi import POICategory
from app.parsing.schema import POIRequirement
from app.parsing.rules.core import Span, _normalize, _iter_free

_POI_PATTERNS: list[tuple[re.Pattern[str], POICategory]] = [
    (re.compile(r"(?<!вид на )(?<!видом на )\bшкол\w+"), POICategory.SCHOOL),
    (re.compile(r"\bсадик\w*|\bдетск\w+\s+сад\w+"), POICategory.KINDERGARTEN),
    (re.compile(r"\bмагазин\w*|\bпродукт\w+|\bсупермаркет\w*"), POICategory.SHOP),
    (re.compile(r"\bпарковк\w*|\bпаркинг\w*"), POICategory.PARKING),
    (re.compile(r"(?<!вид на )(?<!видом на )\b(?:лес\w*|парк\w*|зелен\w+)"), POICategory.PARK_FOREST),
]

_CENTER_PATTERN = re.compile(r"\bв\s+центре(?:\s+москв\w*)?\b")

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
            phrase = text[match.start():match.end()]
            poi_reqs.append(POIRequirement(category=category, raw_phrase=phrase))
            spans.append(match.span())
            
    return poi_reqs, center_requested, sorted(spans)
