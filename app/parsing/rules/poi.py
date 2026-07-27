import re

from app.geo.poi import POICategory
from app.parsing.rules.core import (
    _DIST_MARKER,
    _DIST_NUM,
    _DIST_UNIT,
    _PROXIMITY_MARKER,
    Span,
    _iter_free,
    _normalize,
    _parse_distance_meters,
)
from app.parsing.schema import POIRequirement

#: Префикс перед POI-категорией. Захватываем «нов-» именованной группой ``new``
#: (структурный факт «нужен новый POI» → :attr:`POIRequirement.only_new`).
#: Предлог «с/со» намеренно НЕ захватывается: это чисто грамматическая связка
#: («рядом со школой»), не несущая фильтрующего смысла для pik.ru.
PREFIX = r"(?:(?:с|со)\s+)?(?P<new>\bнов\w+\s+)?"
#: Хвост после POI-категории. Слова «поблизости/рядом/близко» намеренно НЕ
#: сохраняются: близость к POI — это и есть суть POI-требования, отдельного
#: факта тут нет. Дистанцию («в 300 метрах», «не дальше 500 м», «в 1.5 км»)
#: захватываем группами ``dist``/``dist_unit`` (общий фрагмент
#: ``app.parsing.rules.core``, переиспользуемый и в ``station_class.py``) →
#: :attr:`POIRequirement.max_distance_m`.
_DISTANCE = rf"(?:\s+(?:{_DIST_MARKER}\s*)?(?P<dist>{_DIST_NUM})\s*(?P<dist_unit>{_DIST_UNIT}))?"
#: Суффиксные формы близости синхронизированы с общим словарём маркеров
#: (``core._PROXIMITY_MARKER``, Milestone AI-20): раньше «недалеко» (без «от»)
#: и голое «рядом» здесь отсутствовали — «школа недалеко» теряла хвост.
#: Предложные формы («у», «к», «около») в суффикс намеренно НЕ входят: после
#: категории POI они вводят СЛЕДУЮЩИЙ объект («школа у метро X»), а не
#: замыкают требование. По той же причине негативные lookahead-ы не дают
#: суффиксу украсть слово, начинающее следующую конструкцию близости:
#: в «детсадом недалеко от метро X» слово «недалеко» принадлежит «недалеко от
#: метро», а не детсаду — иначе маркер метро разрывается и локация теряет
#: контекст.
SUFFIX = (
    r"(?:\s+(?:поблизости(?!\s+от\b)|неподалеку(?!\s+от\b)|неподалёку(?!\s+от\b)"
    r"|недалеко(?!\s+от\b)|рядом(?:\s+с\s+ним\b)?(?!\s+со?\b)"
    r"|близко(?!\s+ко?\b)|вблизи(?!\s+[а-яё])))?"
) + _DISTANCE

#: Разговорное голое «сад» (без слова «детский») тоже означает детсад
#: («новые сады в 300 метрах», «садов поблизости»). Слово «сад» само по себе
#: омонимично топонимам-станциям метро в ед. числе («Ботанический сад»,
#: «Александровский сад» — см. ``app/reference/metro.json``), поэтому голая
#: форма матчится ОСТОРОЖНО в две ступени:
#: 1. Множественное число (``сады``/``садов``/``садам``/``садами``/``садах``) —
#:    однозначно НЕ топоним (те встречаются только в ед. числе) → маркер не
#:    нужен.
#: 2. Единственное число (``сад``) — рискованно, поэтому матчится только при
#:    обязательном (не опциональном) маркере «нов-» перед словом («новый сад»).
#:    Без маркера бытовое «сад» в тексте лучше молча пропустить, чем ложно
#:    сработать на станцию метро.
_SAD_BARE_PLURAL = r"\bсад(?:ы|ов|ам|ами|ах)\b"
_SAD_BARE_SINGULAR_WITH_MARKER = re.compile(r"(?:(?:с|со)\s+)?(?P<new>\bнов\w+\s+)\bсад\b" + SUFFIX)

_POI_PATTERNS: list[tuple[re.Pattern[str], POICategory]] = [
    (
        # ``(?!ьник)`` отсекает «школьник/школьником/школьника» (человек, не объект):
        # открытый стем ``\bшкол\w+`` иначе ловит «со школьником» как school-POI.
        # Формы объекта «школа/школы/школьная» сохраняются.
        re.compile(PREFIX + r"(?<!вид на )(?<!видом на )\bшкол(?!ьник)\w+" + SUFFIX),
        POICategory.SCHOOL,
    ),
    (
        re.compile(PREFIX + r"(?:\bсадик\w*|\bдетск\w+\s+сад\w*|\bдетсад\w*)" + SUFFIX),
        POICategory.KINDERGARTEN,
    ),
    (
        re.compile(PREFIX + _SAD_BARE_PLURAL + SUFFIX),
        POICategory.KINDERGARTEN,
    ),
    (
        _SAD_BARE_SINGULAR_WITH_MARKER,
        POICategory.KINDERGARTEN,
    ),
    (
        re.compile(PREFIX + r"(?:\bмагазин\w*|\bпродукт\w+|\bсупермаркет\w*)" + SUFFIX),
        POICategory.SHOP,
    ),
    (
        re.compile(PREFIX + r"(?:\bпарковк\w*|\bпаркинг\w*)" + SUFFIX),
        POICategory.PARKING,
    ),
    (
        re.compile(PREFIX + r"(?<!вид на )(?<!видом на )\b(?:лес\w*|парк\w*|зелен\w+)" + SUFFIX),
        POICategory.PARK_FOREST,
    ),
    (
        # Медицина: поликлиника/больница/аптека/роддом/медцентр/клиника/госпиталь.
        # Голое «медицина» намеренно НЕ ловим — избегаем ложных пересечений.
        re.compile(
            PREFIX + r"(?:\bполиклиник\w*|\bбольниц\w*|\bаптек\w*|\bроддом\w*"
            r"|\bмедцентр\w*|\bмед\.?\s*центр\w*|\bклиник\w*|\bгоспитал\w*)" + SUFFIX
        ),
        POICategory.MEDICAL,
    ),
]

_CENTER_PATTERN = re.compile(
    r"\b(?:в\s+центре|к\s+центру|близко\s+к\s+центру|ближе\s+к\s+центру|"
    r"вблизи\s+центра|около\s+центра)(?:\s+москв\w*)?\b"
)

#: ВЕДУЩАЯ дистанция: проксимити-маркер + дистанция ДО категорий («рядом вблизи
#: до 18 минут должны быть школы и сады»). Суффиксный ``_DISTANCE`` такое не
#: ловит — там дистанция идёт ПОСЛЕ категории. Применяется к POI-требованиям без
#: собственной дистанции (см. extract_poi_requirements).
_LEADING_DISTANCE = re.compile(
    _PROXIMITY_MARKER
    + rf"[\s\w]{{0,25}}?(?:{_DIST_MARKER}\s*)?(?P<dist>{_DIST_NUM})\s*(?P<dist_unit>{_DIST_UNIT})"
)

#: ХВОСТОВАЯ дистанция через разрыв: «до этих детских садов БЫЛО ИДТИ до 15-20
#: минут». Суффиксный ``_DISTANCE`` требует дистанцию вплотную к категории, а
#: ведущий ``_LEADING_DISTANCE`` допускает зазор только ПЕРЕД категориями —
#: получалась асимметрия: «до 18 минут … школы и сады» работало, а та же мысль в
#: обратном порядке терялась.
#:
#: Две защиты от кражи чужого фильтра (риск ровно того класса, что «однушка у МЦД
#: от 60 метров» = площадь):
#: 1. Зазор БЕЗ цифр и знаков препинания. Это и есть главный предохранитель:
#:    в «детский сад площадью от 35 до 45 метров» чтобы дотянуться до маркера
#:    «до», зазору пришлось бы проглотить «35» — цифра его обрывает. Запятая
#:    обрывает так же, отделяя соседнее пожелание.
#: 2. Маркер дистанции ОБЯЗАТЕЛЕН (в суффиксной форме он необязателен): через
#:    разрыв голое «N метров» слишком легко оказывается чужим числом.
#:
#: Применяется только к тому POI, чей спан непосредственно предшествует (а не ко
#: всем без дистанции, как ведущая форма): через разрыв связь с конкретной
#: категорией и так на грани, распространять её на соседей нельзя.
_TRAILING_DISTANCE = re.compile(
    rf"\s[^\d,;.!?()]{{0,22}}?{_DIST_MARKER}\s*(?P<dist>{_DIST_NUM})\s*(?P<dist_unit>{_DIST_UNIT})"
)


def extract_poi_requirements(text: str) -> tuple[list[POIRequirement], bool, list[Span]]:
    """Извлечь POI-потребности и требование 'в центре'.

    Каждое вхождение POI разбирается независимо (per-instance): один текст может
    содержать и «новые сады» (``only_new=True``), и просто «школы»
    (``only_new=False``). Аналогично дистанция («в 300 метрах») привязана к
    конкретному POI, а не ко всему результату.

    Сознательно НЕ сохраняются (нет фильтра на pik.ru и/или нет смысловой
    нагрузки для фильтра): предлог «с/со» перед категорией и слова близости
    «поблизости/рядом/близко» в хвосте — их span всё равно засчитывается как
    «понятый», но отдельного структурного факта они не несут (см. комментарии
    у ``PREFIX``/``SUFFIX``).

    Категория ``KINDERGARTEN`` дополнительно матчит разговорное голое «сад»
    без слова «детский» («новые сады», «садов») — см. комментарии у
    ``_SAD_BARE_PLURAL``/``_SAD_BARE_SINGULAR_WITH_MARKER`` о защите от
    ложных срабатываний на топонимы-станции метро («Ботанический сад»).
    """
    norm = _normalize(text)
    poi_reqs: list[POIRequirement] = []
    req_ends: list[tuple[POIRequirement, int]] = []
    spans: list[Span] = []

    # Сначала проверяем 'в центре'
    center_requested = False
    for match in _iter_free(_CENTER_PATTERN, norm, spans):
        center_requested = True
        spans.append(match.span())

    for pattern, category in _POI_PATTERNS:
        for match in _iter_free(pattern, norm, spans):
            phrase = text[match.start() : match.end()]
            only_new = match.group("new") is not None
            dist_raw = match.group("dist")
            max_distance_m = (
                _parse_distance_meters(dist_raw, match.group("dist_unit"))
                if dist_raw is not None
                else None
            )
            req = POIRequirement(
                category=category,
                raw_phrase=phrase,
                only_new=only_new,
                max_distance_m=max_distance_m,
            )
            poi_reqs.append(req)
            # Конец спана нужен ниже, чтобы искать хвостовую дистанцию именно от
            # этой категории, а не «где-то в тексте».
            req_ends.append((req, match.end()))
            spans.append(match.span())

    # Хвостовая дистанция через разрыв («…садов было идти до 15-20 минут») —
    # строго от конца спана своей категории, см. комментарий у _TRAILING_DISTANCE.
    for req, end in req_ends:
        if req.max_distance_m is not None:
            continue
        tail = _TRAILING_DISTANCE.match(norm, end)
        if tail is None:
            continue
        segment = tail.group(0)
        if "метро" in segment or "станц" in segment:
            continue  # время до метро (timeOnFoot), а не дистанция до POI
        req.max_distance_m = _parse_distance_meters(tail.group("dist"), tail.group("dist_unit"))
        spans.append(tail.span())

    # Ведущая дистанция ДО категорий («до 18 минут ... школы и сады») —
    # применяем к POI-требованиям, у которых нет собственной (суффиксной)
    # дистанции. «метро»/«станция» в сегменте пропускаем: это время до метро
    # (URL-фильтр timeOnFoot, rules/time.py), а не дистанция до POI.
    if any(req.max_distance_m is None for req in poi_reqs):
        for match in _iter_free(_LEADING_DISTANCE, norm, spans):
            segment = match.group(0)
            if "метро" in segment or "станц" in segment:
                continue
            meters = _parse_distance_meters(match.group("dist"), match.group("dist_unit"))
            for req in poi_reqs:
                if req.max_distance_m is None:
                    req.max_distance_m = meters
            spans.append(match.span())
            break  # одной ведущей дистанции на фрагмент достаточно

    return _dedupe(poi_reqs), center_requested, sorted(spans)


def _dedupe(poi_reqs: list[POIRequirement]) -> list[POIRequirement]:
    """Схлопнуть повторные упоминания одной категории в одно требование.

    Живой запрос «рядом должны быть детские сады, чтобы до этих детских садов…»
    даёт два одинаковых KINDERGARTEN: категория упомянута дважды, но пожелание
    одно. На URL дубль не влияет (``blocks`` те же), зато засоряет
    ``build_query_signature`` — ключ семантического кэша, из-за чего один и тот
    же запрос мог не попасть в собственный кэш.

    Ключ — ``(категория, only_new)``: «новые сады» и просто «сады» остаются
    РАЗНЫМИ требованиями (первое строго сильнее, схлопывать их — терять смысл).
    Дистанции сливаются по минимуму — самое строгое ограничение побеждает, как
    в :func:`app.geo.candidates._landmark_radius`.
    """
    merged: dict[tuple[str, bool], POIRequirement] = {}
    for req in poi_reqs:
        key = (req.category.value, req.only_new)
        seen = merged.get(key)
        if seen is None:
            merged[key] = req
            continue
        distances = [d for d in (seen.max_distance_m, req.max_distance_m) if d is not None]
        seen.max_distance_m = min(distances) if distances else None
    return list(merged.values())
