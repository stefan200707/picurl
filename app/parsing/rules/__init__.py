"""Regex-правила извлечения структурных фактов из свободного текста (промпт 04)."""

from typing import NamedTuple

from app.parsing.schema import Criteria

from .area import AreaFacts as AreaFacts
from .area import extract_area
from .core import Span, _normalize
from .finish import extract_finish, extract_ready
from .floor import FloorFacts as FloorFacts
from .floor import extract_floor as extract_floor
from .misc import (
    extract_fallback_metro,
    extract_housing_type,
    extract_only_available,
    extract_required_tags,
    extract_settlement_year,
    extract_sort,
    extract_unsupported,
)
from .price import PriceFacts as PriceFacts
from .price import extract_price as extract_price
from .rooms import extract_rooms as extract_rooms
from .time import TimeFacts as TimeFacts
from .time import extract_time_to_metro as extract_time_to_metro


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
    if price.price_min is not None:
        criteria.price_min = price.price_min
    if price.price_max is not None:
        criteria.price_max = price.price_max
    consumed.extend(spans)

    area, spans = extract_area(norm)
    if area.area_min is not None:
        criteria.area_min = area.area_min
    if area.area_max is not None:
        criteria.area_max = area.area_max
    if area.area_kitchen_min is not None:
        criteria.area_kitchen_min = area.area_kitchen_min
    if area.area_kitchen_max is not None:
        criteria.area_kitchen_max = area.area_kitchen_max
    consumed.extend(spans)

    time, spans = extract_time_to_metro(norm)
    if time.time_on_foot is not None:
        criteria.time_on_foot = time.time_on_foot
    if time.time_on_transport is not None:
        criteria.time_on_transport = time.time_on_transport
    consumed.extend(spans)

    floor, spans = extract_floor(norm)
    if floor.floor_min is not None:
        criteria.floor_min = floor.floor_min
    if floor.floor_max is not None:
        criteria.floor_max = floor.floor_max
    if floor.not_first_floor:
        criteria.not_first_floor = True
    if floor.last_floor:
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
        criteria.settlement_year_from = y_min
    if y_max is not None:
        criteria.settlement_year_to = y_max
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
    fallback_metro_tuples = list(zip(fm_names, spans, strict=False))

    return RulesOutcome(
        criteria=criteria,
        consumed=consumed,
        unsupported=unsupported,
        fallback_metro=fallback_metro_tuples,
    )
