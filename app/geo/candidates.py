import json

from app.ai.schema import ComplexCandidate
from app.geo.distance import haversine
from app.parsing.schema import Criteria, LandmarkRequirement, POIRequirement
from app.reference.loader import DATA_DIR, load_all, normalize

#: Максимум ЖК-кандидатов, уходящих в ИИ (шорт-лист держим коротким, чтобы
#: контекст модели оставался фокусным и дешёвым, но при fallback давал выбор).
#: Для запросов «рядом с ориентиром» короткий лимит безопасен и даже желателен:
#: ранжирование по дистанции — детерминированное (app/geo/distance.haversine),
#: поэтому в шорт-лист попадают именно ближайшие ЖК, а не «побольше на глаз».
SHORTLIST_LIMIT = 50


def _location_filter(criteria: Criteria) -> set[str] | None:
    """Множество нормализованных имён локаций из запроса (район/округ/метро).

    Возвращает ``None``, если пользователь не сузил запрос локацией — тогда
    гео-фильтр не применяется. Иначе шорт-лист оставляет только те ЖК, чья
    привязка (``district``/``county``/``metro``) совпадает с запросом, вместо
    произвольных «первых N» из справочника.
    """
    names = {
        normalize(e.name)
        for field in ("districts", "counties", "metro")
        for e in getattr(criteria, field)
    }
    return names or None


def build_candidate_shortlist(criteria: Criteria) -> list[ComplexCandidate]:
    ref_data = load_all()
    poi_cache_path = DATA_DIR / "poi_cache.json"
    poi_cache = json.loads(poi_cache_path.read_text("utf-8")) if poi_cache_path.exists() else {}

    # Маппинг «имя района -> признак центра» для вычисления is_center кандидата.
    center_by_district = {
        normalize(d.name): d.is_center for d in ref_data.districts if d.is_center is not None
    }

    allowed_ids = {c.id for c in criteria.complexes if c.id}
    location_names = None if allowed_ids else _location_filter(criteria)

    # Для запросов «рядом с ориентиром» усечение до SHORTLIST_LIMIT должно идти
    # ПОСЛЕ сортировки по дистанции — иначе «первые N из справочника» отсекут
    # реально ближайшие ЖК. Поэтому при наличии ориентира собираем всех
    # подходящих кандидатов, а лимит применяем в конце.
    rank_by_landmark = bool(criteria.landmark_requirements)

    def _get_candidates(loc_names: set[str] | None) -> list[ComplexCandidate]:
        result = []
        for c in ref_data.complexes:
            # Если пользователь назвал конкретные ЖК — берём только их.
            if allowed_ids and c.id not in allowed_ids:
                continue

            # Иначе, если запрос сужен локацией — оставляем только совпадающие ЖК.
            if loc_names is not None:
                c_locations = {normalize(v) for v in (c.district, c.county, c.metro) if v}
                if loc_names.isdisjoint(c_locations):
                    continue

            known_poi = {}
            if c.slug and c.slug in poi_cache:
                for cat, data in poi_cache[c.slug].items():
                    known_poi[cat] = data.get("count", 0) > 0

            result.append(
                ComplexCandidate(
                    id=c.id or "",
                    name=c.name,
                    district=c.district,
                    county=c.county,
                    metro=[c.metro] if c.metro else [],
                    is_center=center_by_district.get(normalize(c.district)) if c.district else None,
                    known_poi=known_poi,
                    lat=c.lat,
                    lon=c.lon,
                )
            )

            if not rank_by_landmark and len(result) >= SHORTLIST_LIMIT:
                break
        return result

    candidates = _get_candidates(location_names)

    # Fallback: если жесткий гео-фильтр отсёк всех кандидатов (например, ложное
    # срабатывание fuzzy-поиска метро), пробуем без него.
    if not candidates and location_names is not None and not allowed_ids:
        candidates = _get_candidates(None)

    if rank_by_landmark:
        candidates = _rank_by_landmark(candidates, criteria.landmark_requirements)

    return candidates[:SHORTLIST_LIMIT]


def _rank_by_landmark(
    candidates: list[ComplexCandidate], landmarks: list[LandmarkRequirement]
) -> list[ComplexCandidate]:
    """Отфильтровать и отсортировать ЖК по дистанции до ориентиров.

    Чистая математика (:func:`app.geo.distance.haversine`), без обращения к ИИ:
    для каждого кандидата берём минимальное расстояние до любого из заданных
    ориентиров. Кандидаты без координат уходят в конец (их близость неизвестна,
    молча отбрасывать нельзя — пусть достаются ИИ, если он вообще нужен). Если у
    ориентира задан ``max_distance_m`` — применяем жёсткую отсечку.
    """
    max_distance = min(
        (lm.max_distance_m for lm in landmarks if lm.max_distance_m is not None),
        default=None,
    )

    scored: list[tuple[float, ComplexCandidate]] = []
    unknown: list[ComplexCandidate] = []
    for c in candidates:
        if c.lat is None or c.lon is None:
            unknown.append(c)
            continue
        dist = min(haversine(lm.lat, lm.lon, c.lat, c.lon) for lm in landmarks)
        if max_distance is not None and dist > max_distance:
            continue
        scored.append((dist, c))

    scored.sort(key=lambda pair: pair[0])
    ranked = [c for _dist, c in scored]

    # Кандидаты без координат добавляем только когда нет жёсткой отсечки по
    # дистанции (иначе их нельзя гарантированно отнести к «в радиусе»).
    if max_distance is None:
        ranked.extend(unknown)
    return ranked


def resolve_known_facts(candidates: list[ComplexCandidate], criteria: Criteria) -> dict:
    ref_data = load_all()
    center_district_ids = [d.id for d in ref_data.districts if d.is_center and d.id]

    poi_findings = {}
    matched_complex_ids = []

    for c in candidates:
        poi_findings[c.id] = dict(c.known_poi)

        satisfies = True
        if criteria.poi_requirements:
            for req in criteria.poi_requirements:
                if (
                    req.category.value not in c.known_poi
                    or c.known_poi[req.category.value] is not True
                ):
                    satisfies = False
                    break

        # Центральность — статический факт справочника (район ЖК). Кандидат
        # подходит под «в центре», только если он заведомо в центральном районе.
        # Неизвестный центр (is_center=None) не считаем совпадением — такой
        # случай уводит fully_resolved в ИИ, а не додумывается молча.
        if criteria.center_requested and c.is_center is not True:
            satisfies = False

        if satisfies:
            matched_complex_ids.append(c.id)

    return {
        "matched_complex_ids": matched_complex_ids,
        "center_district_ids": center_district_ids,
        "poi_findings": poi_findings,
    }


def fully_resolved(
    known: dict, criteria: Criteria, candidates: list[ComplexCandidate] | None = None
) -> bool:
    """Можно ли ответить на запрос детерминированно, без вызова ИИ.

    Центр разрешим из справочника (флаг ``is_center`` района), но только когда
    он известен у всех кандидатов; хоть один неизвестный (``None``) — уходим в
    ИИ. Аналогично POI: категория обязана присутствовать в ``known_poi`` каждого
    кандидата, иначе факт не подтверждён и нужен ИИ.
    """
    if criteria.center_requested:
        for c in candidates or []:
            if c.is_center is None:
                return False

    if criteria.poi_requirements:
        for _cid, findings in known.get("poi_findings", {}).items():
            for req in criteria.poi_requirements:
                if req.category.value not in findings:
                    return False
    return True


def _poi_signature(req: POIRequirement) -> str:
    """Стабильная строка-подпись POI-требования (категория + структурные факты).

    Учитывает ``only_new`` и ``max_distance_m``, чтобы «сады» и «новые сады в
    300 метрах» не схлопывались в один ключ семантического кэша.
    """
    parts = [req.category.value]
    if req.only_new:
        parts.append("new")
    if req.max_distance_m is not None:
        parts.append(f"d{req.max_distance_m}")
    return ":".join(parts)


def build_query_signature(text: str, criteria: Criteria) -> str:
    """Сигнатура запроса для семантического кэша.

    Кроме нормализованного текста включает управляющие ИИ-обогащением сигналы
    (POI-категории, требование «нового» POI, дистанцию и флаг центра), чтобы кэш
    различал запросы, совпадающие по словам, но требующие разного обогащения
    (например, «сады» и «новые сады» — разные требования).
    """
    poi = sorted(_poi_signature(req) for req in criteria.poi_requirements)
    parts = [text.lower().strip()]
    if poi:
        parts.append("poi=" + ",".join(poi))
    if criteria.center_requested:
        parts.append("center=1")
    if criteria.landmark_requirements:
        landmarks = sorted(normalize(lm.name) for lm in criteria.landmark_requirements)
        parts.append("landmark=" + ",".join(landmarks))
    return " | ".join(parts)
