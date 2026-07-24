import json

from app.ai.schema import ComplexCandidate
from app.geo.distance import CENTER_RADIUS_M, haversine
from app.parsing.schema import (
    Criteria,
    LandmarkRequirement,
    POIRequirement,
    StationClassRequirement,
)
from app.reference.loader import DATA_DIR, load_all, normalize

#: Максимум ЖК-кандидатов, уходящих в ИИ (шорт-лист держим коротким, чтобы
#: контекст модели оставался фокусным и дешёвым, но при fallback давал выбор).
#: Для запросов «рядом с ориентиром» короткий лимит безопасен и даже желателен:
#: ранжирование по дистанции — детерминированное (app/geo/distance.haversine),
#: поэтому в шорт-лист попадают именно ближайшие ЖК, а не «побольше на глаз».
SHORTLIST_LIMIT = 50

#: Радиус «рядом» по умолчанию (метры), когда пользователь не указал точную
#: дистанцию («рядом с МГУ» без «в 500 метрах»). Сознательно переиспользуем
#: CENTER_RADIUS_M (app/geo/distance.py, 5 км — тот же порядок величины, что и
#: эвристика «центр Москвы») вместо нового произвольного числа: единый масштаб
#: «районного» расстояния по проекту. Это эвристика, а не точная величина —
#: как и другие калибруемые пороги проекта (semantic-кэш, confidence
#: промоушена), при появлении данных может потребовать калибровки. Используется
#: только для решения «какие ЖК считать совпадением» (resolve_known_facts/
#: fully_resolved); ранжирование самого шорт-листа (_rank_by_landmark)
#: продолжает отдавать всех кандидатов отсортированными по дистанции без этой
#: отсечки, если явный max_distance_m не задан — так у ИИ (если запрос требует
#: ещё и его) остаётся выбор шире одного жёсткого радиуса.
LANDMARK_DEFAULT_RADIUS_M = CENTER_RADIUS_M

#: Радиус «рядом» по умолчанию для класса станций (Milestone AI-15: «рядом с
#: МЦД не важно какой станции», «у любого метро») — тот же детерминированный
#: механизм (haversine), что и у ориентиров выше, но точка не одна, а ближайшая
#: станция подходящего класса. Значение меньше LANDMARK_DEFAULT_RADIUS_M
#: (5 км, «районный» масштаб): 1500 м — эвристика пешей доступности до станции
#: (~15-20 минут шагом), а не «где-то в том же районе». Как и другие
#: калибруемые пороги проекта (семантический кэш, confidence промоушена), это
#: приближение, требующее калибровки на реальных данных, а не точная величина.
STATION_CLASS_DEFAULT_RADIUS_M = 1500.0

#: Сколько ближайших ЖК показывать, если в радиусе по умолчанию
#: (``STATION_CLASS_DEFAULT_RADIUS_M``) не нашлось ни одного (Milestone AI-18).
#: Живой прогон «в районе коричневой ветки»/«на кольце» показал: это не баг
#: парсинга и не ложное срабатывание, а факт портфеля ПИК — внутри Садового
#: кольца (где ходит Кольцевая линия) новостроек у застройщика нет, ближайший
#: ЖК оказывается в 2.5+ км. Молчаливо возвращать пустую выдачу в такой
#: ситуации формально верно (инвариант «ничего не теряется» соблюдён — есть
#: warning), но бесполезно пользователю: сайт вполне может показать ближайшие
#: варианты. N=8 — компромисс: достаточно, чтобы пользователь мог дальше сам
#: отсеять по цене/комнатности несколько вариантов (в реальных данных для
#: Кольцевой линии это Первый Дубровский ~2.51 км, Руставели 14 ~3.56 км,
#: Барклая 6 ~3.74 км, Волжский парк ~5.24 км и, возможно, ещё несколько), но
#: не настолько много, чтобы под видом «ближайших» подсунуть половину каталога
#: ПИК без всякой связи с запрошенной линией. Как и другие калибруемые пороги
#: проекта — эвристика, не точная величина.
STATION_CLASS_FALLBACK_LIMIT = 8


def _landmark_radius(landmarks: list[LandmarkRequirement]) -> float:
    """Действующий радиус «рядом» для списка требований-ориентиров.

    Явно заданный пользователем ``max_distance_m`` (минимальный среди
    нескольких ориентиров — самое строгое ограничение) побеждает; иначе —
    :data:`LANDMARK_DEFAULT_RADIUS_M`.
    """
    declared = [lm.max_distance_m for lm in landmarks if lm.max_distance_m is not None]
    return min(declared) if declared else LANDMARK_DEFAULT_RADIUS_M


def _station_class_radius(requirements: list[StationClassRequirement]) -> float:
    """Действующий радиус «рядом» для списка требований по классу станций.

    Аналог :func:`_landmark_radius`: явная ``max_distance_m`` (самая строгая
    среди нескольких требований) побеждает; иначе — умолчание
    :data:`STATION_CLASS_DEFAULT_RADIUS_M`.
    """
    declared = [r.max_distance_m for r in requirements if r.max_distance_m is not None]
    return min(declared) if declared else STATION_CLASS_DEFAULT_RADIUS_M


def _matching_station_points(ref_data, line_prefix: str) -> list[tuple[float, float]]:
    """Координаты станций metro.json, подходящих под класс/линию.

    ``line_prefix == "метро"`` — особый случай «любая станция метро вообще»
    (пользователь явно сказал, что линия не важна, без указания класса):
    подходит любая станция справочника с известными координатами, включая
    МЦД/МЦК (они физически являются частью карты метро). Иначе — станции, чьё
    поле ``RefEntry.line`` совпадает с запрошенным классом по префиксу
    (нормализованное сравнение): ``"МЦД"`` матчит и «МЦД», и «МЦД-1»/«МЦД-2»…;
    ``"МЦД-2"`` матчит только эту конкретную линию. У пересадочных станций
    ``line`` может перечислять несколько линий через « / » (см. RefEntry) —
    матчим, если подходит хотя бы одна из них. Станции без координат или без
    заполненного ``line`` (справочник ещё не обогащён — см. CLAUDE.md, раздел
    про справочники) в подходящие не попадают — сужение по ним просто
    невозможно, что вызывающий код обязан отразить явным warning'ом, а не
    тихо проигнорировать.
    """
    prefix_norm = normalize(line_prefix)
    points: list[tuple[float, float]] = []
    for entry in ref_data.metro:
        if entry.lat is None or entry.lon is None:
            continue
        if prefix_norm == "метро":
            points.append((entry.lat, entry.lon))
            continue
        if not entry.line:
            continue
        lines = [normalize(part) for part in entry.line.split("/")]
        if any(line.startswith(prefix_norm) for line in lines):
            points.append((entry.lat, entry.lon))
    return points


def station_class_points(criteria: Criteria) -> list[tuple[float, float]]:
    """Все точки станций, подходящих хотя бы под одно из требований класса.

    Несколько ``station_class_requirements`` трактуются как «класс1 ИЛИ
    класс2»: ЖК подходит, если он в радиусе хотя бы от ОДНОЙ станции хотя бы
    одного из требований. Публичная функция (не приватная): переиспользуется
    и в этом модуле (ранжирование/резолвинг), и в ``app.ai.enrichment``
    (всегда включённая ветка без ИИ) для явного warning'а, когда координат
    станций подходящего класса нет вовсе.
    """
    if not criteria.station_class_requirements:
        return []
    ref_data = load_all()
    points: list[tuple[float, float]] = []
    for requirement in criteria.station_class_requirements:
        points.extend(_matching_station_points(ref_data, requirement.line_prefix))
    return points


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
    # подходящих кандидатов, а лимит применяем в конце. Класс станций
    # (Milestone AI-15) — тот же приём; при одновременном наличии обоих типов
    # запроса (редкий случай) приоритет отдаём ориентиру — конкретная точка
    # точнее, чем «любая станция класса».
    rank_by_landmark = bool(criteria.landmark_requirements)
    rank_by_station_class = bool(criteria.station_class_requirements) and not rank_by_landmark
    rank_by_distance = rank_by_landmark or rank_by_station_class

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
            poi_distances = {}
            if c.slug and c.slug in poi_cache:
                for cat, data in poi_cache[c.slug].items():
                    known_poi[cat] = data.get("count", 0) > 0
                    poi_distances[cat] = data.get("closest_distance_m")

            result.append(
                ComplexCandidate(
                    id=c.id or "",
                    name=c.name,
                    district=c.district,
                    county=c.county,
                    metro=[c.metro] if c.metro else [],
                    is_center=center_by_district.get(normalize(c.district)) if c.district else None,
                    known_poi=known_poi,
                    poi_distances=poi_distances,
                    lat=c.lat,
                    lon=c.lon,
                )
            )

            if not rank_by_distance and len(result) >= SHORTLIST_LIMIT:
                break
        return result

    candidates = _get_candidates(location_names)

    # Fallback: если жесткий гео-фильтр отсёк всех кандидатов (например, ложное
    # срабатывание fuzzy-поиска метро), пробуем без него.
    if not candidates and location_names is not None and not allowed_ids:
        candidates = _get_candidates(None)

    if rank_by_landmark:
        candidates = _rank_by_landmark(candidates, criteria.landmark_requirements)
    elif rank_by_station_class:
        candidates = _rank_by_station_class(candidates, criteria.station_class_requirements)

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


def _rank_by_station_class(
    candidates: list[ComplexCandidate], requirements: list[StationClassRequirement]
) -> list[ComplexCandidate]:
    """Отфильтровать и отсортировать ЖК по дистанции до БЛИЖАЙШЕЙ станции класса.

    Аналог :func:`_rank_by_landmark`, но точка не одна: «подходит любая станция
    класса» — для каждого кандидата берём минимальное расстояние до ЛЮБОЙ из
    станций, подходящих хотя бы под одно из ``requirements`` (несколько
    требований трактуются как «класс1 ИЛИ класс2»). Чистая математика
    (:func:`app.geo.distance.haversine`), без обращения к ИИ. Если у станций
    подходящего класса вовсе нет координат (справочник ещё не обогащён),
    сужение физически невозможно — кандидатов возвращаем как есть, без
    сортировки/отсечки; вызывающий код (``app.ai.enrichment.enrich``) обязан
    отразить это явным warning'ом, а не молчать.
    """
    criteria_stub = Criteria(station_class_requirements=requirements)
    points = station_class_points(criteria_stub)
    if not points:
        return candidates

    max_distance = min(
        (r.max_distance_m for r in requirements if r.max_distance_m is not None),
        default=None,
    )

    scored: list[tuple[float, ComplexCandidate]] = []
    unknown: list[ComplexCandidate] = []
    for c in candidates:
        if c.lat is None or c.lon is None:
            unknown.append(c)
            continue
        dist = min(haversine(lat, lon, c.lat, c.lon) for lat, lon in points)
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


def _station_class_distances(
    candidates: list[ComplexCandidate], requirements: list[StationClassRequirement]
) -> list[tuple[float, ComplexCandidate]]:
    """Дистанции (метры) до БЛИЖАЙШЕЙ станции класса для кандидатов с координатами.

    Кандидаты без ``lat``/``lon`` в расчёт не берутся (близость для них
    недоказуема — это уже отражено отдельным warning'ом выше по стеку, здесь
    не дублируем). Отсортировано по возрастанию дистанции.
    """
    points = station_class_points(Criteria(station_class_requirements=requirements))
    if not points:
        return []

    scored = [
        (min(haversine(lat, lon, c.lat, c.lon) for lat, lon in points), c)
        for c in candidates
        if c.lat is not None and c.lon is not None
    ]
    scored.sort(key=lambda pair: pair[0])
    return scored


def station_class_nearest_fallback(
    candidates: list[ComplexCandidate],
    requirements: list[StationClassRequirement],
    class_names: str,
) -> tuple[list[ComplexCandidate], str | None]:
    """N ближайших ЖК, когда в радиусе по умолчанию не нашлось ни одного (AI-18).

    Осмысленная деградация вместо пустой выдачи: если станции класса физически
    существуют дальше, чем ``STATION_CLASS_DEFAULT_RADIUS_M``, показываем
    ``STATION_CLASS_FALLBACK_LIMIT`` ближайших ЖК вместо тишины — но **только**
    когда радиус дефолтный. Явная пользовательская дистанция
    (``StationClassRequirement.max_distance_m`` задан хотя бы у одного
    требования) — жёсткая отсечка, а не «примерно рядом»: в этом случае фолбэк
    не применяется вообще, и функция возвращает ``([], None)``, оставляя
    вызывающий код (``app.ai.enrichment.enrich``) с прежним поведением (пустой
    результат + честный warning).

    ``class_names`` — уже отформатированная строка вида ``"«Кольцевая»"``
    (несколько классов — через запятую), используется для текста warning в том
    же виде, что и остальные warning'и этой ветки в ``app.ai.enrichment``.

    Возвращает ``(кандидаты, warning)``: кандидаты отсортированы по дистанции,
    усечение до лимита — ПОСЛЕ сортировки (как и весь остальной ранжирующий
    код в этом модуле). Если фолбэк неприменим (явная дистанция задана) или
    показать вообще нечего (нет ни одной подходящей станции с координатами, ни
    одного кандидата с координатами) — ``([], None)``.
    """
    if any(r.max_distance_m is not None for r in requirements):
        return [], None

    scored = _station_class_distances(candidates, requirements)
    if not scored:
        return [], None

    nearest = scored[:STATION_CLASS_FALLBACK_LIMIT]
    radius_km = STATION_CLASS_DEFAULT_RADIUS_M / 1000
    nearest_km = nearest[0][0] / 1000
    warning = (
        f"в радиусе {radius_km:g} км от станций класса {class_names} ЖК нет; "
        f"показаны ближайшие — от {nearest_km:.2f} км"
    )
    return [c for _dist, c in nearest], warning


def block_ids_by_tag(field: str, name: str) -> list[str]:
    """id ЖК, чья привязка (``district``/``county``/``metro``) совпадает с именем.

    Точное совпадение после нормализации — не приближение «на глаз», а прямое
    сопоставление по факту: привязка ЖК к району/округу/метро сама взята из
    живых данных pik.ru (``block.district``/``block.metro``/
    ``locations.child.name``, см. ``app.reference.refresh``), из тех же live-
    данных, из которых взят и сам id ЖК (подтверждено живыми замерами: ``blocks``
    — единственный локационный query-параметр, который ``api.pik.ru/v2/filter``
    реально проверяет). Используется как первый (самый точный) шаг geo-фолбэка
    для локационных сущностей без достоверного id (``app.pik.location_fallback``,
    аудит validate()). ЖК без собственного id в выдачу не попадают — id нужен,
    чтобы результат можно было положить в ``blocks=``.
    """
    ref_data = load_all()
    needle = normalize(name)
    return [
        c.id for c in ref_data.complexes if c.id and normalize(getattr(c, field) or "") == needle
    ]


def complexes_in_mkad(within: bool) -> list[str]:
    """id ЖК, чьи координаты лежат внутри (``within=True``) или вне МКАД.

    Гео-сужение для желания «внутри/за МКАД» (у pik.ru нет такого URL-фильтра —
    см. docs/pik-url-schema.md). Как и остальной geo-фолбэк, работает по
    ``blocks`` (единственный проверяемый параметр): нужен собственный ``id`` ЖК и
    координаты. Полигон и точечная проверка — ``app.geo.mkad``.
    """
    from app.geo.mkad import point_in_mkad

    ref_data = load_all()
    return [
        c.id
        for c in ref_data.complexes
        if c.id
        and c.lat is not None
        and c.lon is not None
        and point_in_mkad(c.lat, c.lon) == within
    ]


def nearby_block_ids(
    lat: float,
    lon: float,
    radius_m: float = STATION_CLASS_DEFAULT_RADIUS_M,
    limit: int = STATION_CLASS_FALLBACK_LIMIT,
) -> tuple[list[str], float | None, bool]:
    """id ЖК рядом с точкой: в радиусе, либо (фолбэк) N ближайших вне радиуса.

    Второй шаг geo-фолбэка (после :func:`block_ids_by_tag`, когда тег-матч не
    дал результата, но у сущности есть координаты) — та же чистая математика
    (:func:`app.geo.distance.haversine`) и та же схема отсечки/фолбэка на
    ближайшие, что уже применяется для класса станций
    (:func:`station_class_nearest_fallback`, Milestone AI-18), просто
    переиспользованная как самостоятельная функция от произвольной точки, а не
    от ``Criteria``.

    Возвращает ``(id ЖК по возрастанию дистанции, дистанция до ближайшего в
    метрах, найдено ли что-то СТРОГО в радиусе)``. Если вообще нет ни одного ЖК
    с известными координатами и id — ``([], None, False)``; вызывающий код
    обязан явно отразить это в warning'е, а не смолчать.
    """
    ref_data = load_all()
    scored = sorted(
        (
            (haversine(lat, lon, c.lat, c.lon), c.id)
            for c in ref_data.complexes
            if c.lat is not None and c.lon is not None and c.id
        ),
        key=lambda pair: pair[0],
    )
    if not scored:
        return [], None, False

    within = [cid for dist, cid in scored if dist <= radius_m]
    if within:
        return within, scored[0][0], True

    nearest = scored[:limit]
    return [cid for _dist, cid in nearest], nearest[0][0], False


def resolve_known_facts(candidates: list[ComplexCandidate], criteria: Criteria) -> dict:
    ref_data = load_all()
    center_district_ids = [d.id for d in ref_data.districts if d.is_center and d.id]
    # Считаем один раз на весь вызов (не на кандидата) — чистая функция, но
    # незачем перечитывать/пересобирать список точек в цикле.
    station_points = station_class_points(criteria)

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
                # Пользовательская отсечка дистанции («садик в 300 метрах»,
                # Milestone AI-20): closest_distance_m из poi_cache. Дистанция
                # запрошена, но неизвестна — совпадением не считаем (не
                # додумываем; тот же принцип, что у центра/ориентира ниже).
                if req.max_distance_m is not None:
                    dist = c.poi_distances.get(req.category.value)
                    if dist is None or dist > req.max_distance_m:
                        satisfies = False
                        break

        # Центральность — статический факт справочника (район ЖК). Кандидат
        # подходит под «в центре», только если он заведомо в центральном районе.
        # Неизвестный центр (is_center=None) не считаем совпадением — такой
        # случай уводит fully_resolved в ИИ, а не додумывается молча.
        if criteria.center_requested and c.is_center is not True:
            satisfies = False

        # Ориентир («рядом с МГУ») — чистая математика (haversine), без ИИ.
        # Кандидат без координат не может подтвердить близость — не считаем
        # совпадением (аналогично неизвестному центру выше), а не додумываем.
        if criteria.landmark_requirements:
            if c.lat is None or c.lon is None:
                satisfies = False
            else:
                radius = _landmark_radius(criteria.landmark_requirements)
                dist = min(
                    haversine(lm.lat, lm.lon, c.lat, c.lon) for lm in criteria.landmark_requirements
                )
                if dist > radius:
                    satisfies = False

        # Класс станций («рядом с МЦД не важно какой станции») — та же чистая
        # математика, но точка не одна: подходит ЖК в радиусе хотя бы от
        # ОДНОЙ станции подходящего класса. Ни станций с координатами, ни
        # координат самого ЖК нет — совпадением не считаем (аналогично
        # ориентиру/центру выше), а не додумываем.
        if criteria.station_class_requirements:
            if not station_points or c.lat is None or c.lon is None:
                satisfies = False
            else:
                radius = _station_class_radius(criteria.station_class_requirements)
                dist = min(haversine(lat, lon, c.lat, c.lon) for lat, lon in station_points)
                if dist > radius:
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
    кандидата, иначе факт не подтверждён и нужен ИИ. Ориентир
    (``landmark_requirements``) — та же логика: близость доказуема только для
    кандидатов с известными координатами, хоть один без ``lat``/``lon`` считаем
    неполным решением (даже если для него дальше выберут «не совпал»). Класс
    станций (``station_class_requirements``) — аналогично: нужны и координаты
    хотя бы одной подходящей станции, и координаты каждого кандидата.
    """
    if criteria.center_requested:
        for c in candidates or []:
            if c.is_center is None:
                return False

    if criteria.poi_requirements:
        # «Только новые» (only_new) кэш POI пока не различает: count схлопывает
        # обычные и construction:/planned:-теги OSM в одно число. Факт
        # новизны детерминированно не подтверждаем — решение уходит в ИИ
        # (известное ограничение схемы кэша, Milestone AI-20).
        if any(req.only_new for req in criteria.poi_requirements):
            return False
        for _cid, findings in known.get("poi_findings", {}).items():
            for req in criteria.poi_requirements:
                if req.category.value not in findings:
                    return False

    if criteria.landmark_requirements:
        for c in candidates or []:
            if c.lat is None or c.lon is None:
                return False

    if criteria.station_class_requirements:
        if not station_class_points(criteria):
            return False
        for c in candidates or []:
            if c.lat is None or c.lon is None:
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
    if criteria.station_class_requirements:
        classes = sorted(normalize(r.line_prefix) for r in criteria.station_class_requirements)
        parts.append("station_class=" + ",".join(classes))
    return " | ".join(parts)
