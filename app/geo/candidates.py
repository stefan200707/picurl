import json

from pydantic import ValidationError

from app.ai.schema import ComplexCandidate
from app.geo.distance import CENTER_RADIUS_M, STRAIGHT_LINE_NOTE, haversine
from app.geo.poi import (
    POI_CACHE_SCHEMA_VERSION,
    POI_CACHE_STALE_HINT,
    POI_CATEGORY_NOT_CACHED_WARNING,
    POIResult,
)
from app.parsing.schema import (
    Criteria,
    LandmarkRequirement,
    POIRequirement,
    StationClassRequirement,
)
from app.reference.loader import DATA_DIR, find_by_name, load_all, load_metro, normalize

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

#: Сколько ближайших ЖК отдавать на СУПЕРЛАТИВНЫЙ запрос («самую ближайшую к
#: Политеху»), см. :func:`landmark_nearest_ids`. Буквальный ответ — один ЖК, но
#: ЖК ≠ квартира: сверху ещё лягут комнатность/цена/этаж, и единственный
#: «ближайший» дом легко даёт нулевую выдачу — ровно ту бесполезность, ради
#: борьбы с которой заведён STATION_CLASS_FALLBACK_LIMIT. N=3 — компромисс:
#: пользователь видит именно ближайшие варианты (а не «районные» полгорода в
#: радиусе 5 км) и всё же имеет из чего выбрать. Эвристика, требующая
#: калибровки на живых прогонах, а не точная величина.
LANDMARK_NEAREST_LIMIT = 3

#: Сколько ближайших ЖК показывать, когда обычное «рядом с X» дало пусто в
#: радиусе по умолчанию (:func:`landmark_nearest_fallback`). Здесь пользователь
#: НЕ просил минимума дистанции — он просил «рядом», и мы честно сообщаем, что
#: рядом ничего нет, показывая варианты пошире. Отсюда лимит больше, чем у
#: суперлатива, и совпадает со станционным STATION_CLASS_FALLBACK_LIMIT: тот же
#: повод (осмысленная деградация вместо тишины) — тот же масштаб выборки.
LANDMARK_FALLBACK_LIMIT = 8

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

#: Максимальная дистанция (метры) от ЖК до ЯКОРНОЙ точки запроса, при которой
#: тег-матч по ИМЕНИ локации (``district``/``county``/``metro``) считается
#: географически правдоподобным (дефект Д3, 2026-08-04).
#:
#: Тег-матч сравнивает нормализованные строки, а имена районов не уникальны по
#: стране: единственный ЖК с ``district="Ломоносовский"`` — питерский
#: «Таллинский парк», и запрос про московский Ломоносовский район получал его
#: как «совпадение». Портфель ПИК общероссийский (Казань, Екатеринбург,
#: Владивосток, Южно-Сахалинск), поэтому таких коллизий будет больше.
#:
#: Порог — «тот же регион», а не «тот же квартал»: точность внутри региона
#: обеспечивает сам тег, задача этой проверки — только отсечь чужой город.
#: Значение выбрано по ФАКТИЧЕСКИМ данным справочника (замер 2026-08-04, 71 ЖК,
#: 357 станций с координатами): самый дальний московский ЖК от самой дальней
#: станции метро — 80.7 км («Зелёный парк» ↔ «Ипподром»), ближайший иногородний
#: ЖК до любой станции — 228.4 км («Волга парк» ↔ «Лобня»). 150 км лежит
#: посередине с запасом ~1.9x к легитимной стороне и ~1.5x к отбрасываемой.
#: Обоснование целиком — docs/thresholds-rationale.md.
TAG_MATCH_ANCHOR_RADIUS_M = 150_000.0

#: Текст об отброшенном тег-матче. Инвариант 1: ЖК, отсеянный как омоним, — это
#: отброшенный факт, о нём обязаны сказать, причём назвав ЖК поимённо (иначе
#: утверждение непроверяемо).
TAG_MATCH_HOMONYM_WARNING = (
    "привязка ЖК {names} к названной локации — совпадение названия, а не места: "
    "они дальше {radius_km:g} км от запрошенных станций, в шорт-лист не взяты"
)

#: Состав общегородского шорт-листа «ближайших по расстоянию»: сколько ЖК
#: отсеяно как чужой регион и сколько — как ЖК без координат. Та же логика, что
#: у ``_rank_by_landmark`` («кандидатов без координат добавляем только когда нет
#: жёсткой отсечки по дистанции»): если список СОБРАН по расстоянию до названной
#: станции, ЖК без координат «ближайшим» назвать нечем. Счётчик, а не имена:
#: список усекается до :data:`SHORTLIST_LIMIT` и без этого отсева, поимённое
#: перечисление двух десятков ЖК другого региона только утопило бы в шуме
#: соседние строки.
SHORTLIST_REGION_TRIM_WARNING = (
    "шорт-лист «ближайших» ограничен регионом запроса: не взяты {other_region} ЖК "
    "другого региона и {unlocatable} ЖК без координат в справочнике"
)


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


def _split_by_geo_plausibility(
    candidates: list[ComplexCandidate], anchors: list[tuple[float, float]]
) -> tuple[list[ComplexCandidate], list[ComplexCandidate]]:
    """Разделить тег-матч на географически правдоподобный и омонимичный (Д3).

    ``anchors`` — точки, относительно которых судим (сейчас это координаты
    названных пользователем станций метро, :func:`_requested_metro_points`). У
    районов и округов координат в справочнике нет, а выдумывать центроид по
    названию запрещает инвариант 3, поэтому **без якорей проверка не
    применяется вовсе** и всё возвращается как есть: это сознательный
    компромисс — омонимия лечится там, где для неё есть данные, а запрос без
    станций работает ровно как раньше (иначе правка молча выкинула бы весь
    неМосковский портфель ПИК).

    Кандидат без координат правдоподобным считается: доказать его удалённость
    нечем, а молча выбрасывать «на всякий случай» — то же додумывание, которого
    избегает весь остальной гео-код (см. :func:`resolve_known_facts`).
    """
    if not anchors:
        return candidates, []

    plausible: list[ComplexCandidate] = []
    homonyms: list[ComplexCandidate] = []
    for c in candidates:
        if c.lat is None or c.lon is None:
            plausible.append(c)
            continue
        dist = min(haversine(lat, lon, c.lat, c.lon) for lat, lon in anchors)
        (homonyms if dist > TAG_MATCH_ANCHOR_RADIUS_M else plausible).append(c)
    return plausible, homonyms


def _parse_poi_entry(slug: str, category: str, data: dict) -> POIResult:
    """Прочитать запись POI-кэша в типизированный :class:`POIResult`.

    Записи прошлой схемы (без ``schema_version``) валидны — у них просто
    ``schema_version=1``, и вызывающий код обязан учитывать, что их дистанция
    считалась вместе со стройками. Битая запись — явная ошибка с инструкцией
    пересобрать кэш, а не тихая деградация в «POI нет».
    """
    try:
        return POIResult.model_validate(data)
    except ValidationError as e:
        raise ValueError(
            f"повреждённая запись POI-кэша {slug}/{category}: {e}; {POI_CACHE_STALE_HINT}"
        ) from e


def _has_stale_poi_entries(poi_cache: dict) -> bool:
    """Есть ли в кэше записи прошлой схемы с непроверяемой дистанцией."""
    for categories in poi_cache.values():
        if not isinstance(categories, dict):
            continue
        for data in categories.values():
            if not isinstance(data, dict):
                continue
            if (
                data.get("schema_version", 1) < POI_CACHE_SCHEMA_VERSION
                and data.get("closest_distance_m") is not None
            ):
                return True
    return False


def build_candidate_shortlist(
    criteria: Criteria, warnings: list[str] | None = None
) -> list[ComplexCandidate]:
    """Шорт-лист ЖК-кандидатов для гео-сужения.

    ``warnings`` необязателен (много вызовов в тестах обходятся без него), но
    вызывающий рантайм обязан его передавать: при откате гео-фильтра на
    общегородской список пользователь должен узнать, что его локация не
    применилась (инвариант 1) — см. :func:`_get_candidates` ниже.
    """
    ref_data = load_all()
    poi_cache_path = DATA_DIR / "poi_cache.json"
    poi_cache = json.loads(poi_cache_path.read_text("utf-8")) if poi_cache_path.exists() else {}

    # Кэш прошлой схемы читается (падать на нём нельзя — рантайм не обязан
    # ломаться из-за формата), но и молчать нельзя: в v1 closest_distance_m
    # считался по ВСЕМ объектам OSM, включая стройплощадки, и как «дистанция до
    # действующего сада» это число недостоверно. Предупреждаем один раз и только
    # если дистанция там реально есть (записи-заглушки промоушена с None ничего
    # не искажают).
    if warnings is not None and _has_stale_poi_entries(poi_cache):
        warnings.append(POI_CACHE_STALE_HINT)

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

    def _get_candidates(
        loc_names: set[str] | None, collect_all: bool = False
    ) -> list[ComplexCandidate]:
        # ``collect_all`` отключает раннюю отсечку по SHORTLIST_LIMIT так же, как
        # её отключает ``rank_by_distance``: если список будет сортироваться по
        # дистанции, усекать его до сортировки нельзя (инвариант 13) — «первые 50
        # из файла» выбросили бы реально ближайшие ЖК.
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
            poi_names = {}
            poi_unnamed = {}
            poi_under_construction = {}
            poi_under_construction_m = {}
            poi_schema_version = {}
            if c.slug and c.slug in poi_cache:
                for cat, data in poi_cache[c.slug].items():
                    entry = _parse_poi_entry(c.slug, cat, data)
                    # v1-запись не различала действующие и строящиеся объекты,
                    # поэтому count_operational там нулевой не по факту, а по
                    # отсутствию данных — берём общий count (иначе кэш прошлой
                    # схемы молча превратил бы «сады есть» в «садов нет»).
                    present = (
                        entry.count_operational
                        if entry.schema_version >= POI_CACHE_SCHEMA_VERSION
                        else entry.count
                    )
                    known_poi[cat] = present > 0
                    poi_distances[cat] = entry.closest_distance_m
                    poi_names[cat] = entry.closest_name
                    poi_unnamed[cat] = entry.closest_unnamed
                    poi_under_construction[cat] = entry.count_under_construction
                    poi_under_construction_m[cat] = entry.closest_under_construction_m
                    poi_schema_version[cat] = entry.schema_version

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
                    poi_names=poi_names,
                    poi_unnamed=poi_unnamed,
                    poi_under_construction=poi_under_construction,
                    poi_under_construction_m=poi_under_construction_m,
                    poi_schema_version=poi_schema_version,
                    lat=c.lat,
                    lon=c.lon,
                )
            )

            if not rank_by_distance and not collect_all and len(result) >= SHORTLIST_LIMIT:
                break
        return result

    # Якоря запроса — единственные точки, по которым можно судить о регионе
    # (координаты есть только у метро; выдумывать центроид района запрещает
    # инвариант 3). Считаем один раз: используем и для Д3-отсечки тег-матча, и
    # для дистанционного ранжирования ниже.
    anchors = _requested_metro_points(criteria) if not allowed_ids else []
    homonyms: list[ComplexCandidate] = []

    candidates = _get_candidates(location_names)

    # Д3: тег-матч сравнивает ИМЕНА, а имена районов не уникальны по стране.
    # Отбрасываем совпадения из другого региона — но только когда есть чем
    # судить, см. _split_by_geo_plausibility.
    if location_names is not None:
        candidates, homonyms = _split_by_geo_plausibility(candidates, anchors)

    # Fallback: если тег-матч не подтвердил запрошенную геометрию, пробуем без
    # него. Два повода, ведущих сюда, объединены намеренно: тег не совпал ни с
    # чем (например, ложное срабатывание fuzzy-поиска метро) ИЛИ каждое
    # совпадение оказалось омонимом из другого региона (Д4 — раньше условие
    # звучало как «список пуст», и один мусорный кандидат блокировал ветку
    # целиком: реальные ЖК рядом с названными станциями в шорт-лист не
    # попадали). В обоих случаях тег непоказателен, а координаты станции —
    # показательны. Молчать при этом нельзя: пользователь просил «в Митино»,
    # получает варианты со всего города, и без предупреждения выдача выглядит
    # как ответ на его запрос (инвариант 1 — ничего не отбрасывается молча).
    # Именно так «ближайшие среди митинских» незаметно становились «ближайшими
    # вообще».
    if not candidates and location_names is not None and not allowed_ids:
        # Если у названной станции есть координаты, «варианты по всему городу» —
        # не единственный выбор: то же самое расстояние, что считает
        # app.pik.location_fallback для итогового blocks=, можно посчитать и
        # здесь. Раньше эти два механизма не знали друг о друге: фолбэк честно
        # находил ближайшие ЖК, а модель получала произвольные первые 50 из
        # файла — и отвечала «подходящих нет», потому что о происхождении
        # списка ей никто не сообщал.
        station_points = anchors if not rank_by_distance else []
        candidates = _get_candidates(None, collect_all=bool(station_points))
        # Д3 действует и здесь: «весь город» — это про ГОРОД запроса. Иначе
        # отсечённый омоним возвращался бы через общегородской список тем же
        # кандидатом и с теми же POI-данными, просто в конце сортировки, — и
        # resolve_known_facts (у него нет отсечки по дистанции) мог объявить
        # его совпадением.
        candidates, other_region = _split_by_geo_plausibility(candidates, anchors)
        unlocatable: list[ComplexCandidate] = []
        if station_points:
            # Шорт-лист этой ветки СОБРАН по расстоянию, и warning ниже прямо
            # обещает «ближайшие». ЖК без координат такому обещанию не
            # соответствует ничем: ни тега, ни дистанции. Раньше он попадал
            # сюда молча и вдобавок ронял fully_resolved (POI-данных у него
            # тоже нет) — то есть требование POI переставало применяться
            # ко ВСЕМ кандидатам из-за ЖК, о котором не известно ничего.
            unlocatable = [c for c in candidates if c.lat is None or c.lon is None]
            candidates = [c for c in candidates if c.lat is not None and c.lon is not None]
            candidates = _rank_by_location_points(candidates, station_points)
        # Здесь отсев считаем ШТУКАМИ, а не именами (в отличие от тег-матча
        # выше): это не потеря совпадения, а состав выборки «ближайших», и
        # она в любом случае усекается до SHORTLIST_LIMIT без перечисления
        # выбывших. Поимённый список на два десятка ЖК другого региона только
        # утопил бы в шуме соседние строки — включая ту, ради которой
        # инвариант 1 и написан.
        if warnings is not None and (other_region or unlocatable):
            warnings.append(
                SHORTLIST_REGION_TRIM_WARNING.format(
                    other_region=len(other_region), unlocatable=len(unlocatable)
                )
            )
        if warnings is not None and candidates:
            requested = ", ".join(
                f"«{e.name}»"
                for field in ("districts", "counties", "metro")
                for e in getattr(criteria, field)
            )
            if station_points and candidates[0].distance_to_location_m is not None:
                nearest_km = candidates[0].distance_to_location_m / 1000
                warnings.append(
                    f"ЖК с привязкой к {requested} в справочнике нет — "
                    f"показаны ближайшие по расстоянию, от {nearest_km:.2f} км {STRAIGHT_LINE_NOTE}"
                )
            else:
                warnings.append(
                    f"ЖК с привязкой к {requested} в справочнике нет — "
                    f"локация не применена, показаны варианты по всему городу"
                )

    # Инвариант 1: ЖК, отсеянный как омоним, — отброшенный факт, и о нём
    # сообщается ОДНОЙ строкой на оба слоя выше (тег-матч и общегородской
    # список), чтобы одна и та же потеря не читалась как две разные.
    if warnings is not None and homonyms:
        warnings.append(
            TAG_MATCH_HOMONYM_WARNING.format(
                names=", ".join(f"«{c.name}»" for c in homonyms),
                radius_km=TAG_MATCH_ANCHOR_RADIUS_M / 1000,
            )
        )

    # Категория, которой нет в кэше ни у одного кандидата, не удовлетворяется
    # никем: resolve_known_facts требует known_poi[cat] is True. Шорт-лист
    # схлопнулся бы в ноль, и снаружи это неотличимо от честного «подходящих ЖК
    # нет». Требование при этом остаётся в силе — мы лишь перестаём делать вид,
    # что оно проверено (инвариант 1).
    if warnings is not None and candidates:
        for category in dict.fromkeys(r.category for r in criteria.poi_requirements):
            if not any(category.value in c.known_poi for c in candidates):
                warnings.append(POI_CATEGORY_NOT_CACHED_WARNING.format(category=category.value))

    if rank_by_landmark:
        candidates = _rank_by_landmark(candidates, criteria.landmark_requirements)
    elif rank_by_station_class:
        candidates = _rank_by_station_class(candidates, criteria.station_class_requirements)

    return candidates[:SHORTLIST_LIMIT]


def _requested_metro_points(criteria: Criteria) -> list[tuple[float, float]]:
    """Координаты названных пользователем станций метро (те, что есть в справочнике).

    Только метро: у районов и округов в ``reference/*.json`` координат нет, а
    выдумывать центроид по названию — ровно то, чего инвариант 3 не разрешает.
    Станция без координат просто не попадает в список: она не мешает остальным.
    """
    points: list[tuple[float, float]] = []
    metro_ref = load_metro()
    for entity in criteria.metro:
        entry = find_by_name(metro_ref, entity.name)
        if entry is not None and entry.lat is not None and entry.lon is not None:
            points.append((entry.lat, entry.lon))
    return points


def _rank_by_location_points(
    candidates: list[ComplexCandidate], points: list[tuple[float, float]]
) -> list[ComplexCandidate]:
    """Отсортировать кандидатов по дистанции до ближайшей из точек локации.

    Жёсткой отсечки по радиусу здесь НЕТ намеренно. Итоговый список ЖК всё
    равно определяет :mod:`app.pik.location_fallback` (там свой радиус и лимит);
    задача этой сортировки — чтобы модель увидела ближайших ПЕРВЫМИ и знала их
    расстояние. Отсекать второй раз и по другому правилу значило бы завести
    третий независимый механизм там, где проблемой была именно
    рассогласованность двух.

    Кандидаты без координат уходят в конец с ``distance_to_location_m=None``:
    молча выбрасывать их нельзя (у них может быть всё остальное), но и
    утверждать про них близость нечем.
    """
    scored: list[tuple[float, ComplexCandidate]] = []
    unknown: list[ComplexCandidate] = []
    for c in candidates:
        if c.lat is None or c.lon is None:
            unknown.append(c)
            continue
        c.distance_to_location_m = min(haversine(lat, lon, c.lat, c.lon) for lat, lon in points)
        scored.append((c.distance_to_location_m, c))

    scored.sort(key=lambda pair: pair[0])
    return [c for _dist, c in scored] + unknown


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


def _landmark_distances(
    candidates: list[ComplexCandidate], landmarks: list[LandmarkRequirement]
) -> list[tuple[float, ComplexCandidate]]:
    """Дистанции (метры) до БЛИЖАЙШЕГО из ориентиров, по возрастанию.

    Аналог :func:`_station_class_distances`. Кандидаты без координат или без
    ``id`` в расчёт не берутся: «ближайшим» их назвать нечем, а без ``id`` они
    всё равно не попадут в ``blocks=``. Явный ``max_distance_m`` (минимальный
    среди требований — самое строгое) остаётся жёсткой отсечкой.
    """
    if not landmarks:
        # Публичная точка входа: без ориентиров считать нечего, а `min()` по
        # пустой последовательности внутри цикла упал бы ValueError.
        return []

    max_distance = min(
        (lm.max_distance_m for lm in landmarks if lm.max_distance_m is not None),
        default=None,
    )

    scored: list[tuple[float, ComplexCandidate]] = []
    for c in candidates:
        if c.lat is None or c.lon is None or not c.id:
            continue
        dist = min(haversine(lm.lat, lm.lon, c.lat, c.lon) for lm in landmarks)
        if max_distance is not None and dist > max_distance:
            continue
        scored.append((dist, c))

    scored.sort(key=lambda pair: pair[0])
    return scored


def landmark_nearest_ids(
    candidates: list[ComplexCandidate],
    landmarks: list[LandmarkRequirement],
    limit: int = LANDMARK_NEAREST_LIMIT,
) -> list[str]:
    """id ближайших к ориентиру ЖК для суперлативного запроса (Milestone AI-22).

    «Самую ближайшую к Политеху» — это не «рядом с Политехом»:
    :data:`LANDMARK_DEFAULT_RADIUS_M` тут не применяется вовсе. Пользователь
    просит МИНИМУМ дистанции, и ответ существует всегда, пока есть хоть один ЖК
    с координатами — даже если ближайший лежит за «районным» радиусом. Радиусная
    трактовка на живом прогоне давала одно из двух: полгорода в 5 км либо пустую
    выдачу там, где рядом действительно ничего нет.

    Явный ``max_distance_m`` («ближайшую в пределах 3 км») остаётся жёсткой
    отсечкой — то же правило, что у :func:`station_class_nearest_fallback`:
    заданная пользователем дистанция сильнее эвристики. Кандидаты без координат
    пропускаются — «ближайшим» их назвать нечем. Усечение до ``limit`` — ПОСЛЕ
    сортировки (инвариант 13).
    """
    ids, _nearest_m = landmark_nearest(candidates, landmarks, limit)
    return ids


def landmark_nearest(
    candidates: list[ComplexCandidate],
    landmarks: list[LandmarkRequirement],
    limit: int = LANDMARK_NEAREST_LIMIT,
) -> tuple[list[str], float | None]:
    """То же, что :func:`landmark_nearest_ids`, плюс дистанция до ближайшего (м).

    Дистанция нужна вызывающему коду для честного warning'а: «показаны
    ближайшие» без числа скрывает от пользователя, что «ближайший» — за 23 км
    (инвариант 14 — дистанции реальные, не шаблонный текст).
    """
    scored = _landmark_distances(candidates, landmarks)
    if not scored:
        return [], None
    return [c.id for _dist, c in scored[:limit]], scored[0][0]


def landmark_nearest_fallback(
    candidates: list[ComplexCandidate],
    landmarks: list[LandmarkRequirement],
    names: str,
) -> tuple[list[ComplexCandidate], str | None]:
    """N ближайших ЖК, когда в радиусе «рядом с X» не нашлось ни одного.

    Полный аналог :func:`station_class_nearest_fallback` (Milestone AI-18), но
    точка — конкретный ориентир: пустая выдача формально верна (инвариант
    «ничего не теряется» соблюдён — warning есть), но бесполезна пользователю,
    если ЖК просто лежат чуть дальше «районного» радиуса.

    Отличие от :func:`landmark_nearest_ids` — в поводе, а не в математике: там
    суперлатив («самую ближайшую»), где радиус не применяется НИКОГДА; здесь
    обычное «рядом с X», где радиус применён и дал пусто. Явная
    ``max_distance_m`` — жёсткая отсечка: фолбэк не запускается, и прежнее
    поведение (пусто + честный warning) остаётся правильным ответом.

    Возвращает ``(кандидаты, warning)``; усечение до лимита — ПОСЛЕ сортировки.
    """
    if any(lm.max_distance_m is not None for lm in landmarks):
        return [], None

    scored = _landmark_distances(candidates, landmarks)
    if not scored:
        return [], None

    nearest = scored[:LANDMARK_FALLBACK_LIMIT]
    warning = (
        f"в радиусе {LANDMARK_DEFAULT_RADIUS_M / 1000:g} км от {names} ЖК нет; "
        f"показаны ближайшие — от {nearest[0][0] / 1000:.2f} км {STRAIGHT_LINE_NOTE}"
    )
    return [c for _dist, c in nearest], warning


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
        f"показаны ближайшие — от {nearest_km:.2f} км {STRAIGHT_LINE_NOTE}"
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


def _collected_poi_categories() -> set[str]:
    """Категории, реально собранные в ``poi_cache.json`` хотя бы для одного ЖК.

    Источник правды о том, что мы вообще умеем проверять. Новая категория
    (добавили энум и OSM-тег, кэш ещё не пересобрали) сюда не попадает — и
    требование по ней честно считается непроверяемым, а не «невыполненным».
    Файл небольшой (десятки записей), кэшировать чтение незачем: так не
    появится расхождения после пересбора кэша в том же процессе.
    """
    path = DATA_DIR / "poi_cache.json"
    if not path.exists():
        return set()
    cache = json.loads(path.read_text("utf-8"))
    return {cat for entry in cache.values() for cat in entry}


def resolve_known_facts(candidates: list[ComplexCandidate], criteria: Criteria) -> dict:
    ref_data = load_all()
    center_district_ids = [d.id for d in ref_data.districts if d.is_center and d.id]
    # Считаем один раз на весь вызов (не на кандидата) — чистая функция, но
    # незачем перечитывать/пересобирать список точек в цикле.
    station_points = station_class_points(criteria)

    poi_findings = {}
    matched_complex_ids = []

    # Категория, которой нет в САМОМ КЭШЕ, — пробел наших данных, а не факт о
    # ЖК: требование по ней не проверяется и потому никого не отсеивает, иначе
    # «мы такую категорию не собирали» выдавалось бы за «подходящих ЖК нет».
    #
    # Считаем по файлу кэша, а НЕ по known_poi текущих кандидатов: второе не
    # различает «категорию не собирали» и «у этих конкретных ЖК данных нет
    # вовсе» (например, шорт-лист целиком из ЖК без координат). Пробел у
    # ОТДЕЛЬНОГО кандидата трактуется по-прежнему — близость недоказуема,
    # требование не пройдено. Пользователь узнаёт о пробеле из
    # POI_CATEGORY_NOT_CACHED_WARNING (его публикует build_candidate_shortlist).
    collected = _collected_poi_categories()
    verifiable_requirements = [
        req for req in criteria.poi_requirements if req.category.value in collected
    ]

    for c in candidates:
        poi_findings[c.id] = dict(c.known_poi)

        satisfies = True
        if verifiable_requirements:
            for req in verifiable_requirements:
                if (
                    req.category.value not in c.known_poi
                    or c.known_poi[req.category.value] is not True
                ):
                    satisfies = False
                    break
                # Пользовательская отсечка дистанции («садик в 300 метрах»,
                # Milestone AI-20): closest_distance_m из poi_cache — это
                # дистанция до ДЕЙСТВУЮЩЕГО объекта (схема кэша v2). Раньше она
                # считалась по всем элементам OSM, и «садик в 200 метрах»
                # проходил по стройплощадке (живой прогон ЖК «Нарвин»: сад в
                # 186 м = relation/13512774, огороженный котлован без названия).
                # Дистанция запрошена, но неизвестна — совпадением не считаем
                # (не додумываем; тот же принцип, что у центра/ориентира ниже).
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

    # Д2: «сужение реально применялось» — ФАКТ этого расчёта, а не догадка
    # вызывающего. Требование учитывается, только если его вообще было чем
    # проверить: непроверяемое POI-требование (категории нет в кэше) из
    # verifiable_requirements уже исключено, и при пустом остатке цикл выше
    # объявляет совпавшими ВСЕХ кандидатов — vacuous truth, который снаружи
    # неотличим от честного «мы посчитали» (тот же класс, что Milestone AI-21).
    # Ориентир/класс станций/центр перечислены явно: каждый из них — отдельная
    # ось отсечки внутри цикла.
    narrowing_applied = bool(
        verifiable_requirements
        or criteria.center_requested
        or criteria.landmark_requirements
        or criteria.station_class_requirements
    )

    return {
        "matched_complex_ids": matched_complex_ids,
        "center_district_ids": center_district_ids,
        "poi_findings": poi_findings,
        "narrowing_applied": narrowing_applied,
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
        # «Только новые» (only_new) кэш POI по-прежнему не различает. Схема v2
        # отделила ДЕЙСТВУЮЩИЕ объекты от строящихся (construction:/planned:/
        # proposed:), но «новый» сад — это уже открытый и недавно построенный, а
        # такого признака в OSM-тегах нет вовсе (дата постройки не заполняется).
        # То есть отделение стройки эту неопределённость не снимает. Факт
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
