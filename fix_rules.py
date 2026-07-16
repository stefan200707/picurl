import re

with open("app/parsing/rules_new/misc.py", "r") as f:
    misc_content = f.read()
with open("app/parsing/rules_new/misc.py", "w") as f:
    f.write("from datetime import datetime\n" + misc_content)

with open("app/parsing/rules_new/__init__.py", "r") as f:
    init_content = f.read()
# Let's just rewrite __init__.py cleanly to export only what's needed
with open("app/parsing/rules_new/__init__.py", "w") as f:
    f.write('''"""Regex-правила извлечения структурных фактов из свободного текста (промпт 04)."""

from typing import NamedTuple
from app.parsing.schema import Criteria

from .core import Span, _normalize
from .rooms import extract_rooms
from .price import extract_price
from .area import extract_area
from .time import extract_time_to_metro
from .floor import extract_floor
from .finish import extract_finish, extract_ready
from .misc import (
    extract_settlement_year, extract_sort, extract_required_tags,
    extract_housing_type, extract_only_available, extract_unsupported,
    extract_fallback_metro
)

class RulesOutcome(NamedTuple):
    """Результат работы всех правил агрегатора."""
    criteria: Criteria
    consumed: list[Span]
    unsupported: list[tuple[str, str]]
    fallback_metro: list[tuple[str, Span]]

def apply_rules(text: str) -> RulesOutcome:
    """Прогнать все правила по тексту и собрать итоговый Criteria."""
    norm = _normalize(text)
    criteria = Criteria()
    consumed: list[Span] = []

    rooms, spans = extract_rooms(norm)
    if rooms:
        criteria.rooms.extend(rooms)
        consumed.extend(spans)

    price, spans = extract_price(norm)
    if price.min is not None:
        criteria.price_min = price.min
    if price.max is not None:
        criteria.price_max = price.max
    consumed.extend(spans)

    area, spans = extract_area(norm)
    if area.total_min is not None:
        criteria.area_min = area.total_min
    if area.total_max is not None:
        criteria.area_max = area.total_max
    if area.kitchen_min is not None:
        criteria.kitchen_min = area.kitchen_min
    if area.kitchen_max is not None:
        criteria.kitchen_max = area.kitchen_max
    consumed.extend(spans)

    time, spans = extract_time_to_metro(norm)
    if time.minutes is not None:
        criteria.time_to_metro = time.minutes
    if time.transport is not None:
        criteria.time_to_metro_transport = time.transport
    consumed.extend(spans)

    floor, spans = extract_floor(norm)
    if floor.min is not None:
        criteria.floor_min = floor.min
    if floor.max is not None:
        criteria.floor_max = floor.max
    if floor.not_first:
        criteria.not_first_floor = True
    if floor.last:
        criteria.last_floor = True
    consumed.extend(spans)

    finish_list, spans = extract_finish(norm)
    if finish_list:
        criteria.finish = finish_list
        consumed.extend(spans)

    ready, spans = extract_ready(norm)
    if ready is not None:
        criteria.ready = ready
        consumed.extend(spans)

    y_min, y_max, spans = extract_settlement_year(norm)
    if y_min is not None:
        criteria.settlement_min = y_min
    if y_max is not None:
        criteria.settlement_max = y_max
    consumed.extend(spans)

    sort_val, spans = extract_sort(norm)
    if sort_val is not None:
        criteria.sort = sort_val
        consumed.extend(spans)

    tags, spans = extract_required_tags(norm)
    if tags:
        criteria.required_tags.extend(tags)
        consumed.extend(spans)

    housing, spans = extract_housing_type(norm)
    if housing is not None:
        criteria.housing_type = housing
        consumed.extend(spans)

    only_avail, spans = extract_only_available(norm)
    if only_avail:
        criteria.only_available = True
        consumed.extend(spans)

    unsupported, spans = extract_unsupported(norm)
    consumed.extend(spans)

    fm_names, spans = extract_fallback_metro(norm)
    fallback_metro_tuples = list(zip(fm_names, spans))

    return RulesOutcome(
        criteria=criteria,
        consumed=consumed,
        unsupported=unsupported,
        fallback_metro=fallback_metro_tuples,
    )
''')
