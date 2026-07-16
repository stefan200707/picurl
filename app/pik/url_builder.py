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
        path_segments.append(criteria.rooms[0].slug)
    elif len(criteria.rooms) > 1:
        query_params["rooms"] = ",".join(r.id for r in criteria.rooms)

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

    # Общие query-параметры добавляем в конец
    query_params.update(criteria.to_query_dict())

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
