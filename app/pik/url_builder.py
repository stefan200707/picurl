from urllib.parse import urlencode

from app.parsing.schema import Criteria, Finish
from app.pik.location_fallback import combine_with_fallback, resolve_fallback_block_ids


def build_url(criteria: Criteria, warnings: list[str] | None = None) -> str:
    """
    Строит URL pik.ru/search по переданным критериям.

    ``warnings`` необязателен (множество вызовов в тестах и аудитах обходятся без
    него), но рантайм обязан его передавать: пересечение гео-фолбэка с уже
    выбранными ЖК может оказаться пустым, и об этом нельзя молчать (инвариант 1).

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
      (фасад `parse` в `app/parsing/parser.py` проверяет их отсутствие и выдает warnings
      до вызова `build_url`, поэтому здесь дополнительной проверки нет) — С ОДНИМ
      ИСКЛЮЧЕНИЕМ: сущности метро/округа/района без достоверного id (аудит
      validate(), см. `app/pik/location_fallback.py`) не пропадают молча, а
      заменяются проверяемым сужением по `blocks` (единственный локационный
      параметр, который бэкенд валидации реально проверяет).
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
        # Multi-query для локаций — общая сборка id (AUDIT_REPORT 2.3)
        query_params.update(criteria.location_query_dict())

    # 3.5 Geo-фолбэк на blocks для локаций без достоверного id (аудит validate()):
    # метро/округ/район, у которых нет ни slug, ни подтверждённого id, тихо
    # выпадали из ссылки выше — вместо этого сужаем ЖК по привязке/расстоянию.
    # ПЕРЕСЕЧЕНИЕ, а не объединение (AI-23 + Дефект №1 фикс) — общая логика с
    # app.pik.validator.validate вынесена в
    # app.pik.location_fallback.combine_with_fallback (Дефект №2: раньше
    # validator ОБЪЕДИНЯЛ фолбэк вместо пересечения, и они расходились). Оба
    # списка сужают одну и ту же ось «какие ЖК» — OR стирал бы более узкое
    # требование, а criteria.complexes_matched_empty гарантирует, что легитимный
    # ноль от ориентира/POI/центра тоже участвует в пересечении, а не
    # игнорируется как «требования не было».
    fallback = resolve_fallback_block_ids(criteria)
    if fallback.block_ids:
        existing_blocks = [b for b in query_params.get("blocks", "").split(",") if b]
        query_params["blocks"] = ",".join(
            combine_with_fallback(existing_blocks, fallback, criteria, warnings)
        )
    # Заметки о том, КАК сузили (полигон МКАД, ориентир, класс станций),
    # публикует тот, кто их породил. Раньше они доезжали до ответа только через
    # validate(), склеенные в один элемент с текстом про проверку выдачи, — то
    # есть справка о критериях зависела от сетевого вызова, к которому не имеет
    # отношения, и была неотделима от него. Добавляем БЕЗУСЛОВНО, вне ветки
    # выше: при пустом block_ids фолбэк как раз и объясняет, почему фильтр
    # пропущен, и терять это объяснение нельзя (инвариант 1).
    if warnings is not None:
        warnings.extend(fallback.notes)

    # Общие query-параметры добавляем в конец
    query_params.update(criteria.to_query_dict())

    # Сборка URL
    base_url = "https://www.pik.ru/search"
    if path_segments:
        base_url = f"{base_url}/{'/'.join(path_segments)}"

    if query_params:
        return f"{base_url}?{urlencode(query_params, safe=',')}"

    return base_url
