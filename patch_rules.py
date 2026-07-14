import re

with open('app/parsing/rules.py', 'r') as f:
    content = f.read()

time_code = """
# ---------------------------------------------------------------------------
# Время до метро
# ---------------------------------------------------------------------------

class TimeFacts(NamedTuple):
    \"\"\"Время до метро.\"\"\"

    time_on_foot: int | None = None
    time_on_transport: int | None = None

_TIME_ON_FOOT = re.compile(r"\\b(?:до|не\\s+более)\\s+(\\d+)\\s*(?:мин\\w*)?\\s*до\\s*метро\\b")
_TIME_ON_TRANSPORT = re.compile(r"\\b(?:до|не\\s+более)\\s+(\\d+)\\s*(?:мин\\w*)?\\s*(?:на\\s+транспорте|транспортом)\\b")

def extract_time_to_metro(text: str) -> tuple[TimeFacts, list[Span]]:
    \"\"\"Извлечь время до метро.\"\"\"
    norm = _normalize(text)
    spans: list[Span] = []
    time_on_foot: int | None = None
    time_on_transport: int | None = None

    for match in _iter_free(_TIME_ON_FOOT, norm, spans):
        if time_on_foot is None:
            time_on_foot = int(match.group(1))
            spans.append(match.span())

    for match in _iter_free(_TIME_ON_TRANSPORT, norm, spans):
        if time_on_transport is None:
            time_on_transport = int(match.group(1))
            spans.append(match.span())

    return TimeFacts(time_on_foot, time_on_transport), sorted(spans)

# ---------------------------------------------------------------------------
# Этаж
"""

content = content.replace("# ---------------------------------------------------------------------------\n# Этаж", time_code)

content = content.replace("floor, floor_spans = extract_floor(text)", "time_metro, time_metro_spans = extract_time_to_metro(text)\n    floor, floor_spans = extract_floor(text)")
content = content.replace("floor_min=floor.floor_min,", "time_on_foot=time_metro.time_on_foot,\n        time_on_transport=time_metro.time_on_transport,\n        floor_min=floor.floor_min,")
content = content.replace("*area_spans,", "*area_spans,\n            *time_metro_spans,")

with open('app/parsing/rules.py', 'w') as f:
    f.write(content)

