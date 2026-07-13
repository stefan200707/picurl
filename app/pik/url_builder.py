"""Build a pik.ru URL with applied filters from Criteria.

Rule of URL building:
https://www.pik.ru/search/{rooms}/{finish_or_ready}/{location}?query
- Rooms: exactly 1 room goes to path. Multiple go to query parameter `rooms`.
- Finish / Ready: True value adds a segment (`finish` and `ready` respectively) to the path.
- Location: If exactly 1 county AND 0 metros -> county slug goes to path.
            If exactly 1 metro AND 0 counties -> metro slug goes to path.
            Otherwise, all location values go to query parameters.
- Query parameters are strictly ordered to guarantee stability.
"""

from urllib.parse import urlencode

from app.parsing.schema import Criteria, HousingType, Rooms

ROOMS_PATH_MAP = {
    Rooms.STUDIO: "studio",
    Rooms.ONE: "one-room",
    Rooms.TWO: "two-room",
    Rooms.THREE_PLUS: "three-room",
}

ROOMS_QUERY_MAP = {
    Rooms.STUDIO: "-1",
    Rooms.ONE: "1",
    Rooms.TWO: "2",
    Rooms.THREE_PLUS: "3",
}


def build_url(criteria: Criteria) -> str:
    """Build a pik.ru URL with applied filters from Criteria.

    Path priority: rooms -> finish/ready -> location.
    If exactly one room choice, it goes to the path (e.g. `two-room`).
    If exactly one location (county or metro) is given, it goes to the path.
    Otherwise, they go to query parameters.
    """
    path_segments = []

    # 1. Rooms
    rooms_in_path = False
    if len(criteria.rooms) == 1:
        path_segments.append(ROOMS_PATH_MAP[criteria.rooms[0]])
        rooms_in_path = True

    # 2. Finish / Ready
    if criteria.finish:
        path_segments.append("finish")
    if criteria.ready:
        path_segments.append("ready")

    # 3. Location
    loc_path_segment = None
    loc_entity = None
    if len(criteria.counties) == 1 and not criteria.metro:
        if criteria.counties[0].slug:
            loc_path_segment = criteria.counties[0].slug
            loc_entity = ("county", criteria.counties[0])
    elif len(criteria.metro) == 1 and not criteria.counties and criteria.metro[0].slug:
        loc_path_segment = criteria.metro[0].slug
        loc_entity = ("metro", criteria.metro[0])

    if loc_path_segment:
        path_segments.append(loc_path_segment)

    # Populate query parameters
    q = []

    # price
    if criteria.price_min is not None:
        q.append(("priceFrom", str(criteria.price_min)))
    elif criteria.price_max is not None:
        q.append(("priceFrom", "0"))

    if criteria.price_max is not None:
        q.append(("priceTo", str(criteria.price_max)))

    # area
    if criteria.area_min is not None:
        q.append(("areaFrom", str(criteria.area_min)))
    if criteria.area_max is not None:
        q.append(("areaTo", str(criteria.area_max)))

    # areaKitchen
    if criteria.area_kitchen_min is not None:
        q.append(("areaKitchenFrom", str(criteria.area_kitchen_min)))
    if criteria.area_kitchen_max is not None:
        q.append(("areaKitchenTo", str(criteria.area_kitchen_max)))

    # floor
    if criteria.floor_min is not None:
        q.append(("floorFrom", str(criteria.floor_min)))
    if criteria.floor_max is not None:
        q.append(("floorTo", str(criteria.floor_max)))

    if criteria.not_first_floor:
        q.append(("notFirstFloor", "1"))
    if criteria.last_floor:
        q.append(("lastFloor", "1"))

    # time on foot / transport
    if criteria.time_on_foot is not None:
        q.append(("timeOnFoot", str(criteria.time_on_foot)))
    if criteria.time_on_transport is not None:
        q.append(("timeOnTransport", str(criteria.time_on_transport)))

    # sorting
    if criteria.sort is not None:
        q.append(("sortBy", criteria.sort.field))
        q.append(("orderBy", criteria.sort.order))

    # rooms query
    if not rooms_in_path and criteria.rooms:
        q.append(("rooms", ",".join(ROOMS_QUERY_MAP[r] for r in criteria.rooms)))

    # blocks (complexes)
    if criteria.complexes:
        ids = [c.id for c in criteria.complexes if c.id]
        if ids:
            q.append(("blocks", ",".join(ids)))

    # districts
    if criteria.districts:
        ids = [d.id for d in criteria.districts if d.id]
        if ids:
            q.append(("districtLocations", ",".join(ids)))

    # counties
    if not (loc_entity and loc_entity[0] == "county") and criteria.counties:
        ids = [c.id for c in criteria.counties if c.id]
        if ids:
            q.append(("districtCounties", ",".join(ids)))

    # metro
    if not (loc_entity and loc_entity[0] == "metro") and criteria.metro:
        ids = [m.id for m in criteria.metro if m.id]
        if ids:
            q.append(("metroStations", ",".join(ids)))

    # settlement year / month
    if criteria.settlement_year_from is not None:
        q.append(("settlementYearFrom", str(criteria.settlement_year_from)))
    if criteria.settlement_year_to is not None:
        q.append(("settlementYearTo", str(criteria.settlement_year_to)))
    if criteria.settlement_month_from is not None:
        q.append(("settlementMonthFrom", str(criteria.settlement_month_from)))
    if criteria.settlement_month_to is not None:
        q.append(("settlementMonthTo", str(criteria.settlement_month_to)))

    # benefit
    if criteria.current_benefit is not None:
        q.append(("currentBenefit", criteria.current_benefit))

    # optionGroups
    if criteria.option_groups:
        q.append(("optionGroups", ",".join(criteria.option_groups)))

    # options
    if criteria.options:
        q.append(("options", ",".join(criteria.options)))

    # type
    if criteria.housing_type == HousingType.FLATS_ONLY:
        q.append(("type", "1"))

    # status
    if criteria.only_available:
        q.append(("status", "free"))

    base_url = "https://www.pik.ru/search"
    if path_segments:
        base_url += "/" + "/".join(path_segments)

    if q:
        query_string = urlencode(q, safe=",")
        return f"{base_url}?{query_string}"

    return base_url
