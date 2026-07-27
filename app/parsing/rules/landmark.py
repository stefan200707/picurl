"""Распознавание запросов «рядом с ориентиром» (промпт 23).

Ориентир (вуз/работодатель/достопримечательность) — отдельный класс сущности,
которого нет в URL-схеме pik.ru: у сайта нет фильтра «рядом с МГУ». Поэтому
ориентир не превращается в путь/query URL напрямую — он даёт координаты, по
которым :func:`app.geo.candidates.build_candidate_shortlist` детерминированно
сужает список ЖК по дистанции (чистая математика, без ИИ).

Механизм извлечения переиспользует тот же подход, что и матчинг сущностей
(:mod:`app.parsing.entity_match`): маркер близости («рядом с», «у», «поближе к»,
«недалеко от», «ближе к», «около», «возле» + имя) → скользящее окно из
следующих 1–3 слов → нечёткое сопоставление (rapidfuzz) с именами/алиасами
справочника ``landmarks.json``. Отдельный параллельный механизм не заводится.
"""

import re

from rapidfuzz import fuzz, process

from app.parsing.rules.core import (
    _DIST_NUM,
    _DIST_UNIT,
    Span,
    _normalize,
    _parse_distance_meters,
)
from app.parsing.schema import LandmarkRequirement
from app.reference.loader import RefEntry, load_landmarks, normalize

#: Порог доверия нечёткого сопоставления имени ориентира. Держим высоким —
#: маркеры близости («у», «около») частотны, и ложный матч дал бы неверное
#: сужение ЖК; лучше не распознать, чем распознать не тот ориентир.
SCORE_THRESHOLD = 88.0

#: Маркеры близости к ориентиру. Порядок важен для regex-альтернатив: более
#: длинные формы («рядом с», «поближе к») стоят раньше коротких («у», «к»),
#: чтобы жадный движок не откусывал предлог раньше времени.
_MARKER = (
    r"\b(?:"
    r"рядом\s+со|рядом\s+с|"
    r"поближе\s+ко|поближе\s+к|ближе\s+ко|ближе\s+к|"
    r"ближайш\w+\s+ко|ближайш\w+\s+к|"
    r"недалеко\s+от|неподалеку\s+от|неподалёку\s+от|"
    r"вблизи|близко\s+ко|близко\s+к|около|возле|"
    r"у|к"
    r")\s+"
)
_MARKER_PATTERN = re.compile(_MARKER)

#: Суперлативный хвост, стоящий ПЕРЕД маркером близости: «самую ближайшую к»,
#: «ближе всего к». Отличать суперлатив от обычного «рядом с» обязательно — он
#: просит МИНИМУМ дистанции, а не попадание в радиус (см.
#: LandmarkRequirement.nearest_only). Окончания перечислены явно, а не через
#: ``сам\w+``: открытый стем цеплял бы «самолёта у аэропорта» (ср. guard
#: ``школ(?!ьник)`` в rules/poi.py — та же линия «лексикой, а не порогом»).
#: Совпадение расширяет consumed-span влево, иначе «самую» оседает в остатке и
#: даёт ложный warning «не удалось распознать».
#: «максимально»/«как можно» добавлены по живому прогону («максимально близко к
#: МФТИ», «как можно ближе к МГУ»): семантически это тот же суперлатив, но без
#: них признак не ставился, и запрос молча деградировал в радиус 5 км — а у МФТИ
#: в радиусе 5 км нет ни одного ЖК, то есть пользователь получал пустую выдачу
#: там, где правильный ответ существует.
_SUPERLATIVE_PREFIX = re.compile(
    r"(?:\b(?:сам(?:ый|ая|ую|ое|ые|ых|ым|ыми|ой|ому|ом)|наиболее|максимально)"
    r"\s+(?:близк\w+\s+)?"
    r"|\bближе\s+всего\s+|\bкак\s+можно\s+)$"
)

#: Тот же признак суперлатива, но без привязки к позиции маркера — для случая,
#: когда ориентир достал ИИ, а не regex (склонения ниже SCORE_THRESHOLD:
#: «бауманке» → QRatio 87.5). Без него ИИ-путь всегда давал nearest_only=False,
#: и запрос молча деградировал в «рядом» (радиус 5 км).
#: Guard `(?!\s+врем|\s+будущ)`: «сдача в ближайшее время» — это про срок, а не
#: про дистанцию (лексикой, в духе `школ(?!ьник)` в rules/poi.py).
_SUPERLATIVE_CUE = re.compile(
    r"\b(?:ближайш\w+(?!\s+(?:врем|будущ))|ближе\s+всего|как\s+можно\s+ближе"
    r"|(?:наиболее|максимально|сам(?:ый|ая|ую|ое|ые|ых|ым|ыми|ой|ому|ом))\s+близк\w+)"
)


def has_superlative_cue(text: str) -> bool:
    """Есть ли в тексте признак суперлатива («самую ближайшую», «ближе всего»).

    Нужен там, где ориентир пришёл не из этого правила (ИИ-экстрактор,
    :func:`app.ai.enrichment.sanitize_landmark_resolution`), а признак
    ``LandmarkRequirement.nearest_only`` выставить всё равно надо.
    """
    return _SUPERLATIVE_CUE.search(_normalize(text)) is not None


#: Суффиксная дистанция ПОСЛЕ имени ориентира («рядом с МГУ не дальше 1 км»).
#: Механизм общий с poi/station_class (`rules/core.py`), но маркер здесь —
#: суженный: из `_DIST_MARKER` исключены «до» и вариант «без маркера вообще».
#: Причина в коллизии с площадью: имя ориентира ищется нечётким окном, а не
#: regex'ом, поэтому после него легко идёт «площадью до 45 метров» — и голое
#: «до N метров» увело бы площадь в дистанцию (тот же класс, что «однушка у МЦД
#: от 60 метров» = площадь, зафиксированный в station_class). Лучше не распознать
#: дистанцию, чем украсть чужой фильтр.
_LANDMARK_DIST_MARKER = r"(?:не\s+дальше|не\s+более|не\s+далее|в\s+пределах|максимум|в)"
_DISTANCE_SUFFIX = re.compile(
    rf"\s+{_LANDMARK_DIST_MARKER}\s*(?P<dist>{_DIST_NUM})\s*(?P<dist_unit>{_DIST_UNIT})"
)

#: Токен слова-кандидата после маркера (буквы/цифры/точка/дефис).
_WORD = re.compile(r"[а-яёa-z0-9]+(?:[.-][а-яёa-z0-9]+)*")

#: Максимум слов в окне-кандидате имени ориентира.
_MAX_WINDOW = 3


def _landmark_choices() -> list[tuple[str, RefEntry]]:
    """Нормализованные имя+алиасы каждого ориентира (для нечёткого поиска)."""
    choices: list[tuple[str, RefEntry]] = []
    for entry in load_landmarks():
        choices.append((normalize(entry.name), entry))
        for alias in entry.aliases:
            choices.append((normalize(alias), entry))
    return choices


def _best_match(window: str, choices: list[tuple[str, RefEntry]]) -> tuple[RefEntry, float] | None:
    """Лучший ориентир для окна-текста (rapidfuzz WRatio + строгий QRatio)."""
    needle = normalize(window)
    if not needle:
        return None
    choice_strings = [c[0] for c in choices]
    result = process.extractOne(needle, choice_strings, scorer=fuzz.WRatio)
    if result is None:
        return None
    matched_str, score, idx = result
    if score < SCORE_THRESHOLD:
        return None
    # Строгая перепроверка QRatio: WRatio завышает оценку для частичных
    # совпадений (короткое слово внутри длинного имени), QRatio симметричен.
    if fuzz.QRatio(needle, matched_str) < SCORE_THRESHOLD:
        return None
    return choices[idx][1], score


def extract_landmark_requirements(text: str) -> tuple[list[LandmarkRequirement], list[Span]]:
    """Извлечь требования «рядом с ориентиром» из свободного текста.

    Возвращает пару ``(requirements, consumed_spans)``: список найденных
    ориентиров (с координатами из справочника) и диапазоны «понятого» текста
    (маркер + имя) для вычисления нераспознанного в фасаде ``parse``.
    """
    norm = _normalize(text)
    choices = _landmark_choices()
    if not choices:
        return [], []

    reqs: list[LandmarkRequirement] = []
    spans: list[Span] = []
    seen_names: set[str] = set()

    for marker in _MARKER_PATTERN.finditer(norm):
        tail = norm[marker.end() :]
        words = list(_WORD.finditer(tail))
        if not words:
            continue

        best: tuple[RefEntry, float, int] | None = None  # (entry, score, end_offset)
        for n in range(min(_MAX_WINDOW, len(words)), 0, -1):
            window_start = words[0].start()
            window_end = words[n - 1].end()
            window_text = tail[window_start:window_end]
            match = _best_match(window_text, choices)
            if match is None:
                continue
            entry, score = match
            # Больше слов, покрывающих имя целиком, лучше отражают ориентир
            # («московский государственный университет» точнее, чем «московский»).
            if best is None or score > best[1]:
                best = (entry, score, marker.end() + window_end)

        if best is None:
            continue

        entry, _score, end_offset = best
        if entry.lat is None or entry.lon is None:
            continue
        if entry.name in seen_names:
            continue
        seen_names.add(entry.name)

        # Суперлатив: либо сам маркер («ближайшую к»), либо усилитель перед ним
        # («самую ближайшую к», «ближе всего к» — там маркером работает голое
        # «к», и признак виден только слева). Начало спана сдвигаем на усилитель.
        start_offset = marker.start()
        prefix = _SUPERLATIVE_PREFIX.search(norm[:start_offset])
        nearest_only = prefix is not None or marker.group().startswith("ближайш")
        if prefix is not None:
            start_offset = prefix.start()

        # Явная дистанция сразу за именем ориентира — расширяем спан на неё,
        # иначе «не дальше 1 км» осядет в остатке ложным warning'ом. У poi/
        # station_class дистанция входит в тот же regex и попадает в span
        # бесплатно; здесь имя ищется нечётким окном, поэтому доклеиваем вручную.
        max_distance_m: int | None = None
        distance = _DISTANCE_SUFFIX.match(norm, end_offset)
        if distance is not None:
            max_distance_m = _parse_distance_meters(
                distance.group("dist"), distance.group("dist_unit")
            )
            end_offset = distance.end()

        reqs.append(
            LandmarkRequirement(
                name=entry.name,
                lat=entry.lat,
                lon=entry.lon,
                category=entry.category,
                raw_phrase=text[start_offset:end_offset],
                nearest_only=nearest_only,
                max_distance_m=max_distance_m,
            )
        )
        spans.append((start_offset, end_offset))

    return reqs, spans
