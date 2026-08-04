"""Regex-правила извлечения структурных фактов из свободного текста (промпт 04)."""

from collections.abc import Iterable
from typing import NamedTuple

from app.parsing.schema import Criteria

from .area import AreaFacts as AreaFacts
from .area import extract_area
from .core import Span, _normalize
from .finish import extract_finish, extract_ready
from .floor import FloorFacts as FloorFacts
from .floor import extract_floor as extract_floor
from .landmark import extract_landmark_requirements as extract_landmark_requirements
from .misc import (
    extract_fallback_metro,
    extract_housing_type,
    extract_only_available,
    extract_required_tags,
    extract_settlement_year,
    extract_sort,
    extract_unsupported,
    extract_within_mkad,
)
from .poi import extract_poi_requirements as extract_poi_requirements
from .price import PriceFacts as PriceFacts
from .price import extract_price as extract_price
from .rooms import extract_rooms as extract_rooms
from .station_class import (
    extract_station_class_requirements as extract_station_class_requirements,
)
from .time import TimeFacts as TimeFacts
from .time import extract_time_to_metro as extract_time_to_metro


class RulesOutcome(NamedTuple):
    """Результат работы всех правил агрегатора."""

    criteria: Criteria
    consumed: list[Span]
    unsupported: list[tuple[str, str]]
    fallback_metro: list[tuple[str, Span]]
    #: Фрагменты, распознанные правилом, но не применённые: слот уже занят более
    #: ранним значением. Спан списан (иначе число подберёт другое правило), поэтому
    #: молчать о них нельзя — фасад делает из них warning категории ``lost``.
    dropped: tuple[str, ...] = ()


def _blank(norm: str, spans: Iterable[Span]) -> str:
    """Забелить пробелами участки ``spans``, сохранив длину (индексы не съезжают).

    Тот же приём, что в :func:`app.parsing.parser.parse` при подготовке текста
    для матчера сущностей, но в обратную сторону: там правила закрывают свои
    участки от сущностей, здесь сущности — от правил.
    """
    chars = list(norm)
    for start, end in spans:
        chars[start:end] = [" "] * (end - start)
    return "".join(chars)


def apply_rules(text: str, reserved: Iterable[Span] = ()) -> RulesOutcome:
    """Прогнать все правила по тексту и собрать итоговый Criteria.

    ``reserved`` — участки, уже занятые названием сущности справочника
    («Руставели 14»). Они забеливаются в нормализованной копии ДО прогона
    правил, поэтому число внутри названия недоступно ни одному правилу:
    защита от сфабрикованного фильтра — механизм общий, а не пер-правило
    (см. :func:`app.parsing.parser._entity_number_spans`).

    В возвращаемый ``consumed`` ``reserved`` НЕ попадает: этот список
    вычитается из текста перед матчингом сущностей, и, забелив там название,
    мы потеряли бы сам ЖК.
    """
    norm = _blank(_normalize(text), reserved)
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

    # Передаём уже съеденные спаны (цена/площадь/время/комнаты выше): голый паттерн
    # диапазона этажа «от X до Y» иначе повторно матчит эти числа (см. floor.py).
    floor, spans = extract_floor(norm, consumed)
    if floor.floor_min is not None:
        criteria.floor_min = floor.floor_min
    if floor.floor_max is not None:
        criteria.floor_max = floor.floor_max
    if floor.not_first_floor:
        criteria.not_first_floor = True
    if floor.last_floor:
        criteria.last_floor = True
    if floor.not_last_floor:
        criteria.not_last_floor = True
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

    within_mkad, spans = extract_within_mkad(norm)
    if within_mkad is not None:
        criteria.within_mkad = within_mkad
        consumed.extend(spans)

    unsupported, spans = extract_unsupported(norm)
    consumed.extend(spans)

    # Спаны выше (цена/площадь/время/этаж) нужны ведущей дистанции POI: без них
    # «площадью от 35 до 45 метров рядом школа» отдаёт «до 45 метров» школе
    # (см. poi._LEADING_DISTANCE). Тот же приём, что у extract_floor.
    poi_reqs, center_req, spans = extract_poi_requirements(norm, consumed)
    if poi_reqs:
        criteria.poi_requirements.extend(poi_reqs)
    if center_req:
        criteria.center_requested = True
    consumed.extend(spans)

    landmark_reqs, spans = extract_landmark_requirements(norm)
    if landmark_reqs:
        criteria.landmark_requirements.extend(landmark_reqs)
        consumed.extend(spans)

    station_class_reqs, spans = extract_station_class_requirements(norm)
    if station_class_reqs:
        criteria.station_class_requirements.extend(station_class_reqs)
        consumed.extend(spans)

    fm_names, spans = extract_fallback_metro(norm)
    fallback_metro_tuples = list(zip(fm_names, spans, strict=False))

    return RulesOutcome(
        criteria=criteria,
        consumed=consumed,
        unsupported=unsupported,
        fallback_metro=fallback_metro_tuples,
        dropped=area.dropped,
    )
