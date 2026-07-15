from urllib.parse import urlencode

from app.parsing.schema import Criteria, Finish, HousingType, Rooms


def build_url(criteria: Criteria) -> str:
    """
    Строит URL pik.ru/search по переданным критериям.

    Правило single-путь / multi-query:
    - Комнатность в путь, если выбрана ровно одна. Иначе в query.
    - Отделка (`finish`) и заселение (`ready`) в путь. Если выбраны оба,
      порядок: finish, затем ready.
    - Локация в путь, только если выбрана ровно одна локационная сущность всего
      (т.е. 1 метро и 0 округов/районов/ЖК, или 1 округ и 0 метро/районов/ЖК).
      Районы и ЖК всегда в query. Если выбрана ровно 1 сущность и это район или ЖК,
      то она идет в query, а локационного сегмента в пути нет.
    - Если `price_max` задан, а `price_min` нет, то добавляется `priceFrom=0`
      для точного соответствия примеру из ТЗ.
    - Неполные сущности (без id/slug в зависимости от назначения) молча пропускаются
      (ожидается, что validation/warnings слой отработает это ранее или параллельно).
    """
    path_segments = []
    query_params = {}

    # --- Путь ---

    # 1. Комнатность
    if len(criteria.rooms) == 1:
        room_slugs = {
            Rooms.STUDIO: "studio",
            Rooms.ONE: "one-room",
            Rooms.TWO: "two-room",
            Rooms.THREE_PLUS: "three-room",
        }
        path_segments.append(room_slugs[criteria.rooms[0]])
    elif len(criteria.rooms) > 1:
        room_ids = {
            Rooms.STUDIO: "-1",
            Rooms.ONE: "1",
            Rooms.TWO: "2",
            Rooms.THREE_PLUS: "3",
        }
        query_params["rooms"] = ",".join(room_ids[r] for r in criteria.rooms)

    # 2. Отделка и заселение
    if len(criteria.finish) == 1:
        f = criteria.finish[0]
        if f == Finish.READY:
            path_segments.append("finish")
        elif f == Finish.NONE:
            path_segments.append("bez-otdelki")
        elif f == Finish.WHITE_BOX:
            path_segments.append("predchistovaya-otdelka")
        elif f == Finish.FURNISHED:
            query_params["hasFinish"] = str(f.value)
    elif len(criteria.finish) > 1:
        query_params["hasFinish"] = ",".join(str(f.value) for f in criteria.finish)

    if criteria.ready is True:
        path_segments.append("ready")

    # 2.5 Особенности планировки

    # 3. Локация
    total_locations = (
        len(criteria.metro)
        + len(criteria.counties)
        + len(criteria.districts)
        + len(criteria.complexes)
    )

    if total_locations == 1:
        if len(criteria.counties) == 1:
            if criteria.counties[0].slug:
                path_segments.append(criteria.counties[0].slug)
            elif criteria.counties[0].id:
                query_params["districtCounties"] = criteria.counties[0].id
        elif len(criteria.metro) == 1:
            if criteria.metro[0].slug:
                slug = criteria.metro[0].slug
                if not slug.startswith("m-"):
                    slug = f"m-{slug}"
                path_segments.append(slug)
            elif criteria.metro[0].id:
                query_params["metroStations"] = criteria.metro[0].id
        elif len(criteria.districts) == 1 and criteria.districts[0].id:
            query_params["districtLocations"] = criteria.districts[0].id
        elif len(criteria.complexes) == 1 and criteria.complexes[0].id:
            query_params["blocks"] = criteria.complexes[0].id
    else:
        # Multi-query для локаций
        if criteria.counties:
            ids = [c.id for c in criteria.counties if c.id]
            if ids:
                query_params["districtCounties"] = ",".join(ids)
        if criteria.metro:
            ids = [m.id for m in criteria.metro if m.id]
            if ids:
                query_params["metroStations"] = ",".join(ids)
        if criteria.districts:
            ids = [d.id for d in criteria.districts if d.id]
            if ids:
                query_params["districtLocations"] = ",".join(ids)
        if criteria.complexes:
            ids = [c.id for c in criteria.complexes if c.id]
            if ids:
                query_params["blocks"] = ",".join(ids)

    # --- Query ---

    # Цена (добавляем priceFrom=0, если задан только priceTo)
    if criteria.price_min is not None:
        query_params["priceFrom"] = str(criteria.price_min)
    elif criteria.price_max is not None:
        query_params["priceFrom"] = "0"

    if criteria.price_max is not None:
        query_params["priceTo"] = str(criteria.price_max)

    # Площадь
    if criteria.area_min is not None:
        query_params["areaFrom"] = str(criteria.area_min)
    if criteria.area_max is not None:
        query_params["areaTo"] = str(criteria.area_max)

    # Площадь кухни
    if criteria.area_kitchen_min is not None:
        query_params["areaKitchenFrom"] = str(criteria.area_kitchen_min)
    if criteria.area_kitchen_max is not None:
        query_params["areaKitchenTo"] = str(criteria.area_kitchen_max)

    # Этаж
    if criteria.floor_min is not None:
        query_params["floorFrom"] = str(criteria.floor_min)
    if criteria.floor_max is not None:
        query_params["floorTo"] = str(criteria.floor_max)
    if criteria.not_first_floor:
        query_params["notFirstFloor"] = "1"
    if criteria.last_floor:
        query_params["lastFloor"] = "1"
    if criteria.not_last_floor:
        query_params["notLastFloor"] = "1"

    # Время
    if criteria.time_on_foot is not None:
        query_params["timeOnFoot"] = str(criteria.time_on_foot)
    if criteria.time_on_transport is not None:
        query_params["timeOnTransport"] = str(criteria.time_on_transport)

    # Сортировка
    if criteria.sort is not None:
        query_params["sortBy"] = criteria.sort.field
        query_params["orderBy"] = criteria.sort.order

    # Год и месяц сдачи
    if criteria.settlement_year_from is not None:
        query_params["settlementYearFrom"] = str(criteria.settlement_year_from)
    if criteria.settlement_year_to is not None:
        query_params["settlementYearTo"] = str(criteria.settlement_year_to)
    if criteria.settlement_month_from is not None:
        query_params["settlementMonthFrom"] = str(criteria.settlement_month_from)
    if criteria.settlement_month_to is not None:
        query_params["settlementMonthTo"] = str(criteria.settlement_month_to)

    # Программы и опции
    if criteria.current_benefit:
        query_params["currentBenefit"] = criteria.current_benefit
    if criteria.option_groups:
        query_params["optionGroups"] = ",".join(criteria.option_groups)
    if criteria.options:
        query_params["options"] = ",".join(criteria.options)
    if getattr(criteria, "required_tags", None):
        query_params["requiredTags"] = ",".join(criteria.required_tags)

    # Тип и статус
    if criteria.housing_type == HousingType.FLATS_ONLY:
        query_params["type"] = "1"
    if criteria.only_available:
        query_params["status"] = "free"

    # Сборка URL
    base_url = "https://www.pik.ru/search"
    if path_segments:
        base_url = f"{base_url}/{'/'.join(path_segments)}"

    if query_params:
        # Для фиксированного детерминированного порядка параметров в URL
        # (pydantic и dict сохраняют порядок, но лучше отсортировать или задать явный порядок,
        # однако dict с 3.7+ сохраняет порядок вставки, что уже детерминировано)
        return f"{base_url}?{urlencode(query_params, safe=',')}"

    return base_url
