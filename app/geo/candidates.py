import json

from app.ai.schema import ComplexCandidate
from app.parsing.schema import Criteria
from app.reference.loader import DATA_DIR, load_all, normalize

#: Максимум ЖК-кандидатов, уходящих в ИИ (шорт-лист держим коротким, чтобы
#: контекст модели оставался фокусным и дешёвым, но при fallback давал выбор).
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
                )
            )

            if len(result) >= SHORTLIST_LIMIT:
                break
        return result

    candidates = _get_candidates(location_names)

    # Fallback: если жесткий гео-фильтр отсёк всех кандидатов (например, ложное
    # срабатывание fuzzy-поиска метро), пробуем без него.
    if not candidates and location_names is not None and not allowed_ids:
        candidates = _get_candidates(None)

    return candidates


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


def build_query_signature(text: str, criteria: Criteria) -> str:
    """Сигнатура запроса для семантического кэша.

    Кроме нормализованного текста включает управляющие ИИ-обогащением сигналы
    (POI-категории и флаг центра), чтобы кэш различал запросы, совпадающие по
    словам, но требующие разного обогащения.
    """
    poi = sorted(req.category.value for req in criteria.poi_requirements)
    parts = [text.lower().strip()]
    if poi:
        parts.append("poi=" + ",".join(poi))
    if criteria.center_requested:
        parts.append("center=1")
    return " | ".join(parts)
