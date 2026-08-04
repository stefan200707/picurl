from pydantic import BaseModel

from app.geo.poi import POICategory
from app.parsing.schema import Finish, HousingType, Rooms, Sort


class ComplexCandidate(BaseModel):
    id: str
    name: str
    district: str | None
    county: str | None
    metro: list[str]
    is_center: bool | None
    known_poi: dict[str, bool | None]
    #: Дистанция до ближайшего POI категории в метрах (closest_distance_m из
    #: poi_cache.json; None — категория в кэше есть, но дистанция неизвестна).
    #: Нужна детерминированной проверке пользовательского max_distance_m
    #: («садик в 300 метрах», Milestone AI-20) — раньше поле кэша никем не
    #: читалось и требование дистанции фактически игнорировалось.
    #: ВАЖНО (шаг «качество POI-данных»): дистанция считается только по
    #: ДЕЙСТВУЮЩИМ объектам. Стройплощадка в 186 м («сад» relation/13512774 у ЖК
    #: «Нарвин») в отсечку max_distance_m больше не попадает.
    poi_distances: dict[str, float | None] = {}
    #: Имя ближайшего действующего объекта категории (``None`` — безымянный в
    #: OSM). Нужно для прозрачности: пользователь должен видеть, ПОЧЕМУ ЖК
    #: прошёл требование, а не только число метров.
    poi_names: dict[str, str | None] = {}
    #: Ближайший действующий объект категории без ``name`` (на карте — двор).
    #: Отличает «объект действительно безымянный в OSM» от «имени нет в кэше»
    #: (запись v1 имён не сохраняла): в обоих случаях ``poi_names[cat] is None``,
    #: но утверждать безымянность можно только в первом (инвариант 1).
    poi_unnamed: dict[str, bool] = {}
    #: Сколько объектов категории отброшено как строящиеся/проектируемые —
    #: факт сохраняется (инвариант 1), но фильтром не служит.
    poi_under_construction: dict[str, int] = {}
    #: Дистанция до ближайшей стройки категории (``closest_under_construction_m``).
    #: Фильтром не служит, но объясняет живой случай «сад в 186 м»: действующий
    #: сад дальше, а в 186 м — котлован. Читается в ``_warn_poi_evidence``.
    poi_under_construction_m: dict[str, float | None] = {}
    #: Версия схемы записи кэша по категории (1 — стройки не отделены). Нужна,
    #: чтобы пояснение пользователю не утверждало «ближайший ДЕЙСТВУЮЩИЙ» там,
    #: где данных о действующести нет вовсе.
    poi_schema_version: dict[str, int] = {}
    # Координаты ЖК — объективный факт из справочника (complexes.json). Нужны
    # детерминированному слою (build_candidate_shortlist) для сужения/сортировки
    # кандидатов по дистанции до ориентира. Модель их не «прикидывает» — расчёт
    # расстояния делает app/geo/distance.haversine, а не LLM.
    lat: float | None = None
    lon: float | None = None
    #: Дистанция (метры) до запрошенной локации, когда шорт-лист собран именно
    #: как «ближайшие к ней» — например, названная станция метро есть в
    #: справочнике, но ни один ЖК к ней не привязан тегом. Считает haversine, не
    #: модель (инвариант 3): число приходит к ней готовым фактом.
    #:
    #: Без этого поля список выглядел для модели произвольным: она видела 50 ЖК
    #: и запрос «нужно ВДНХ», не находила ВДНХ ни в одном ``metro`` и честно
    #: отвечала «подходящих нет» — хотя перед ней лежали ровно ближайшие к ВДНХ.
    #: ``None`` — шорт-лист собран не по дистанции либо у ЖК нет координат.
    distance_to_location_m: float | None = None


class AIEnrichmentAnswer(BaseModel):
    matched_complex_ids: list[str]
    center_district_ids: list[str] = []
    poi_findings: dict[str, dict[str, bool]] = {}
    explanation: str
    confidence: float


class OptionMatch(BaseModel):
    """Сопоставление нераспознанной фразы с slug'ом опции/группы опций.

    ``slug`` — ``None``, если модель не нашла соответствия (фраза остаётся в
    ``warnings`` как есть, ничего не выдумываем). Значение slug обязательно
    валидируется против реального справочника
    (:func:`app.ai.enrichment.sanitize_option_resolution`) — как для кандидатов
    ЖК, строке из ответа модели не доверяем слепо.
    """

    phrase: str
    slug: str | None = None
    confidence: float = 0.8


class OptionResolutionAnswer(BaseModel):
    """Ответ модели на резолвинг фраз-синонимов фильтров под опции."""

    matches: list[OptionMatch] = []


class LandmarkMatch(BaseModel):
    """Сопоставление нераспознанной фразы с ориентиром из ``landmarks.json``.

    Полный аналог :class:`OptionMatch`: модель возвращает ТОЛЬКО ``slug``, а
    координаты берутся из справочника (:func:`app.ai.enrichment.
    sanitize_landmark_resolution`). Просить у модели lat/lon нельзя — это прямое
    нарушение инварианта «LLM не считает дистанции и не выдумывает фильтры»:
    выдуманная точка молча сдвинула бы гео-сужение. Slug вне справочника
    отбрасывается, фраза остаётся в ``warnings``.
    """

    #: Дословный фрагмент из ``unresolved_fragments`` — проверяется санитайзером,
    #: а не принимается на веру: реальный slug на произвольной фразе иначе молча
    #: сузил бы выдачу по случайному ориентиру.
    phrase: str
    slug: str | None = None


class FinishMatch(BaseModel):
    """Сопоставление нераспознанной фразы со значением :class:`Finish`.

    Та же схема, что у :class:`LandmarkMatch`: модель отдаёт не строку-описание,
    а ЗНАЧЕНИЕ ЭНУМА — вариант вне энума pydantic отбивает ещё при разборе
    ответа. Отделка живёт отдельным сегментом пути (``/finish``) и в справочник
    опций не входит вовсе, поэтому до этой ветки незнакомая формулировка
    («отделка под чистовую», «ремонт от застройщика») уходила в
    ``resolve_options()`` и там не могла совпасть ни с чем в принципе.
    """

    #: Дословный фрагмент из ``unresolved_fragments`` — проверяется санитайзером.
    phrase: str
    finish: Finish | None = None


class POIMatch(BaseModel):
    """Сопоставление нераспознанной фразы с требованием к окружению.

    Модель отдаёт только КАТЕГОРИЮ (энум :class:`POICategory`) и, если она
    названа в тексте, порог дистанции. Сами расстояния до объектов модель не
    считает и не возвращает — их считает haversine по кэшу POI (инвариант 3).
    ``max_distance_m`` здесь — это переписанное из запроса пользователя
    требование («не дальше 500 м»), а не результат вычисления.
    """

    #: Дословный фрагмент из ``unresolved_fragments`` — проверяется санитайзером.
    phrase: str
    category: POICategory | None = None
    max_distance_m: int | None = None
    only_new: bool = False


class FreeTextCriteriaAnswer(BaseModel):
    """Извлечение недостающих СКАЛЯРНЫХ фильтров из свободного текста (ведро C).

    Новый путь (ослабление гейтов): если детерминированный парсер оставил
    значимый остаток («не удалось распознать»), модель пытается достать из текста
    только безопасные скаляры/энумы Criteria, НЕ требующие резолвинга справочника
    (метро/районы/округа/ЖК/опции остаются на детерминированных путях +
    ``sanitize_*``, модель их не трогает).

    Инвариант «ИИ не выдумывает фильтры» соблюдён двумя рубежами: (1) значения
    ограничены схемой (энумы Rooms/Sort/HousingType, границы Criteria при
    присваивании); (2) детерминированный слой всегда выигрывает — заполняются
    лишь ПУСТЫЕ поля (см. :func:`app.ai.enrichment.resolve_free_text_criteria`).
    ``consumed_fragments`` — фрагменты из ``unresolved_fragments``, которые модель
    сопоставила с полем; по ним снимаются warning'и, как в option-резолвинге.

    Исключение из «только скаляры» — ``landmarks`` (Milestone AI-22): ориентиры
    резолвятся здесь же, но по той же схеме, что опции — модель отдаёт лишь slug
    из переданного ей каталога, координаты подставляет справочник. Тем же
    исключением добавлены ``finish`` и ``poi``: модель отдаёт значение энума и
    дословную фразу, объект собирает санитайзер.
    """

    rooms: list[Rooms] = []
    price_min: int | None = None
    price_max: int | None = None
    area_min: float | None = None
    area_max: float | None = None
    area_kitchen_min: float | None = None
    area_kitchen_max: float | None = None
    floor_min: int | None = None
    floor_max: int | None = None
    not_first_floor: bool = False
    last_floor: bool = False
    not_last_floor: bool = False
    ready: bool | None = None
    sort: Sort | None = None
    housing_type: HousingType | None = None
    settlement_year_from: int | None = None
    settlement_year_to: int | None = None
    #: Время до метро, минуты (реальные URL-фильтры timeOnFoot/timeOnTransport).
    #: ИИ-страховка: детерминированное правило rules/time.py может промахнуться по
    #: идиоме («в шаговой доступности»), тогда поле заполнит модель.
    time_on_foot: int | None = None
    time_on_transport: int | None = None
    only_available: bool = False
    #: Ориентиры («рядом с Политехом»): только slug из каталога, см. LandmarkMatch.
    landmarks: list[LandmarkMatch] = []
    #: Отделка и окружение — энум-значения с проверкой ``phrase``, см. FinishMatch/POIMatch.
    finish: list[FinishMatch] = []
    poi: list[POIMatch] = []
    consumed_fragments: list[str] = []
    explanation: str = ""
    confidence: float = 0.0
