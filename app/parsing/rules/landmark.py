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

from app.parsing.rules.core import Span, _normalize
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

        reqs.append(
            LandmarkRequirement(
                name=entry.name,
                lat=entry.lat,
                lon=entry.lon,
                category=entry.category,
                raw_phrase=text[marker.start() : end_offset],
            )
        )
        spans.append((marker.start(), end_offset))

    return reqs, spans
