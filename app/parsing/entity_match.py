"""Sliding-window + rapidfuzz matching of entities against reference data."""

import re
from functools import cache
from typing import NamedTuple

from rapidfuzz import fuzz, process

from app.parsing.rules import Span
from app.parsing.rules.core import _PROXIMITY_MARKER
from app.parsing.rules.core import _normalize as _normalize_chars
from app.parsing.schema import MatchedEntity
from app.parsing.stopwords import LOCATION_MARKERS, STOP_WORDS
from app.reference.loader import RefEntry, load_all, normalize

SCORE_THRESHOLD = 80.0
TRIGGERED_SCORE_THRESHOLD = 75.0
#: Доп. порог QRatio для окон без триггера/ключевого слова и для всех
#: «синтетических» окон (эвристика союзов). WRatio завышает оценку для
#: коротких/служебных слов, случайно похожих на длинное имя сущности
#: («мы» ~ «Мытищи» = 50, «этаж и ищем» ~ «Два и более санузла» = 33,
#: «Бульвар МЦК» ~ «Бульвар Адмирала Ушакова» = 57); держим порог как у основного
#: WRatio-скоринга, чтобы не терять опечатки/склонения (напр. «Бабушкинском
#: районе» ~ «Бабушкинский район» = 86.5, «Рокоссовсого» ~ «Рокоссовского» = 96).
STRICT_QRATIO_THRESHOLD = SCORE_THRESHOLD

#: Записи справочника с очень коротким именем/алиасом (без пробелов, символов
#: <= этого числа — округа-аббревиатуры «САО»/«ВАО»/«НАО»/«ЦАО»…, алиас «юг»,
#: метро «ЗИЛ», район «Уюн») нельзя сравнивать нечётко: на строках длиной 2-4
#: символа WRatio/QRatio легко достигают порога СЛУЧАЙНО, просто из-за общих
#: букв, а не смыслового сходства (предлог «со» ~ «САО» = 80.0/80.0 ровно,
#: «во» ~ «ВАО» = 80.0/80.0 ровно, частица «надо» ~ алиас «НАО» = 85.7/85.7 —
#: см. CLAUDE.md, разбор бага «квартира со сквозным санузлом» → ложный округ
#: САО). Для таких кандидатов требуем точного совпадения нормализованного
#: текста окна вместо порога — это защищает от всего класса коротких
#: служебных слов/частиц разом, а не только от «со» (whack-a-mole со
#: стоп-словами здесь ненадёжен: коротких предлогов/частиц в русском много).
SHORT_ENTITY_EXACT_MAX_LEN = 3

SCORE_BONUS_MARKER = 10.0
SCORE_BONUS_PREP_MATCH = 5.0
SCORE_PENALTY_PREP_MISMATCH = -5.0

HIERARCHY_SCORES = {
    "county": 0.5,
    "district": 0.4,
    "metro": 0.3,
    "complex": 0.2,
    "options": 0.1,
    "option_groups": 0.0,
}


class EntityMatch(NamedTuple):
    type: str  # "metro", "county", "district", "complex"
    entity: MatchedEntity
    score: float
    span: Span


TRIGGERS = [
    # (pattern, type)
    #
    # Маркер близости перед «метро» — ЕДИНЫЙ источник форм из
    # ``rules.core._PROXIMITY_MARKER`` (Milestone AI-20, Фикс 1): раньше здесь
    # был независимый неполный список («у метро»/«рядом с метро»), и «недалеко
    # от метро X» матчил сущность X, но сам маркер оставался «непонятым»
    # текстом → мусорный warning + лишний кандидат в опции (лишний вызов ИИ).
    (rf"(?i)\b{_PROXIMITY_MARKER}\s+метро\s+$", "metro"),
    (r"(?i)\bна\s+метро\s+$", "metro"),
    # Голое «метро » перед именем — самостоятельный однозначный контекст типа
    # (симметрично уже существующим голым «район »/«округ »/«жк » ниже).
    (r"(?i)\bметро\s+$", "metro"),
    (r"(?i)\bм\.\s+$", "metro"),
    (r"(?i)\bм\s+$", "metro"),
    (rf"(?i)\b{_PROXIMITY_MARKER}\s+район[ае]?\s+$", "district"),
    (r"(?i)\bв\s+районе\s+$", "district"),
    (r"(?i)\bрайон\s+$", "district"),
    (rf"(?i)\b{_PROXIMITY_MARKER}\s+округ[ае]?\s+$", "county"),
    (r"(?i)\bокруг\s+$", "county"),
    (r"(?i)\bв\s+юзао\s+$", "county"),
    (r"(?i)\bв\s+жк\s+$", "complex"),
    (r"(?i)\bжк\s+$", "complex"),
]

COMPILED_TRIGGERS = [(re.compile(pat), ttype) for pat, ttype in TRIGGERS]

# STOP_WORDS imported from app.parsing.stopwords


@cache
def build_choices() -> list[tuple[str, str, RefEntry]]:
    data = load_all()
    choices = []
    for etype, entries in [
        ("metro", data.metro),
        ("county", data.counties),
        ("district", data.districts),
        ("complex", data.complexes),
        ("options", data.options),
        ("option_groups", data.option_groups),
    ]:
        for entry in entries:
            choices.append((normalize(entry.name), etype, entry))
            for alias in entry.aliases:
                choices.append((normalize(alias), etype, entry))
    return choices


def get_trigger_type(text_before: str) -> tuple[str, int] | tuple[None, None]:
    """Найти типоспецифичный триггер («у метро », «в районе », …) в конце текста.

    Текст предварительно прогоняется через посимвольную нормализацию
    (:func:`app.parsing.rules.core._normalize`): она сохраняет длину строки,
    поэтому ``m.start()`` остаётся валидным индексом исходного текста, а
    латинские гомоглифы («у мeтро» с латинской 'e') не ломают триггер
    (Milestone AI-20, Фикс 2).
    """
    normalized = _normalize_chars(text_before)
    for pat, ttype in COMPILED_TRIGGERS:
        m = pat.search(normalized)
        if m:
            return ttype, m.start()
    return None, None


def _is_stop_word_window(tokens: list[str]) -> bool:
    return all(t.lower() in STOP_WORDS for t in tokens)


def _sole_location_kw_type(triggered_types: set[str]) -> str | None:
    """Единственный локационный тип из сработавших триггеров окна, либо None.

    Типы опций сюда не входят: kw_partial («сануз»/«вид»…) — подстрочная
    эвристика скоринга, а не однозначный контекст локации.
    """
    location_types = [t for t in ("metro", "district", "county", "complex") if t in triggered_types]
    return location_types[0] if len(location_types) == 1 else None


def _adjust_score(
    score: float, etype: str, window_text: str, text_before: str, entry: RefEntry
) -> float:
    before_tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", text_before)
    before_tokens = [t.lower() for t in before_tokens]
    window_tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", window_text)
    window_tokens = [t.lower() for t in window_tokens]

    last_5_before = before_tokens[-5:] if before_tokens else []
    entry_names = [entry.name.lower()] + [a.lower() for a in entry.aliases]

    def _is_marker_context(markers: list[str]) -> bool:
        for m in markers:
            if m in last_5_before:
                return True
            if m in window_tokens:
                m_in_name = any(re.search(rf"\b{re.escape(m)}\b", name) for name in entry_names)
                if not m_in_name:
                    return True
        return False

    has_jk = _is_marker_context(["жк", "жилой комплекс"])
    has_district = _is_marker_context(["район", "районе"])
    has_county = _is_marker_context(["округ", "округе"])
    has_metro = _is_marker_context(["м", "метро"]) or "м." in last_5_before
    has_option = _is_marker_context(["вид", "видом"])

    new_score = score
    if has_jk and etype == "complex":
        new_score += SCORE_BONUS_MARKER
    if has_district and etype == "district":
        new_score += SCORE_BONUS_MARKER
    if has_county and etype == "county":
        new_score += SCORE_BONUS_MARKER
    if has_metro and etype == "metro":
        new_score += SCORE_BONUS_MARKER
    if has_option and etype in ("options", "option_groups"):
        new_score += SCORE_BONUS_MARKER

    last_word_before = before_tokens[-1] if before_tokens else ""
    first_word_window = window_tokens[0] if window_tokens else ""

    prep_na = (last_word_before == "на") or (first_word_window == "на")
    prep_v = (last_word_before == "в") or (first_word_window == "в")

    if prep_na:
        # Only apply preposition bonus if the first word of the window is part of the entity name
        # (or if the window starts with the preposition itself)
        first_in_name = first_word_window == "на" or any(
            first_word_window in name for name in entry_names
        )
        if first_in_name:
            if etype in ("county", "district"):
                new_score += SCORE_BONUS_PREP_MATCH
            elif etype == "complex":
                new_score += SCORE_PENALTY_PREP_MISMATCH

    if prep_v:
        first_in_name = first_word_window == "в" or any(
            first_word_window in name for name in entry_names
        )
        if first_in_name and etype == "complex":
            new_score += SCORE_BONUS_PREP_MATCH

    new_score += HIERARCHY_SCORES.get(etype, 0.0)

    return new_score


def _build_window_specs(tokens_info, text):
    window_specs = []
    for n in range(1, 6):
        for i in range(len(tokens_info) - n + 1):
            window_tokens = tokens_info[i : i + n]
            start_idx = window_tokens[0][1]
            end_idx = window_tokens[-1][2]
            text_before = text[:start_idx]
            window_specs.append((window_tokens, text_before, start_idx, end_idx, False))

    # Heuristic for conjunctions
    for i, (tok_str, _start, _end) in enumerate(tokens_info):
        if tok_str.lower() in {"и", "или"} and i >= 2 and i + 1 < len(tokens_info):
            w2 = tokens_info[i + 1]
            for n_mod in (1, 2):
                if i - 1 - n_mod >= 0:
                    mod_tokens = tokens_info[i - 1 - n_mod : i - 1]
                    synthetic_tokens = [*mod_tokens, w2]
                    s_start = w2[1]
                    s_end = w2[2]
                    t_before = text[: mod_tokens[0][1]]
                    window_specs.append((synthetic_tokens, t_before, s_start, s_end, True))
    return window_specs


def _score_window(window_tokens, text_before, start_idx, end_idx, is_synthetic, text, choices):
    window_strings = [t[0] for t in window_tokens]

    if _is_stop_word_window(window_strings):
        return None

    original_chunk = text[start_idx:end_idx]
    if "," in original_chunk or ";" in original_chunk:
        return None

    clean_window = " ".join(w for w in window_strings if w.lower() not in STOP_WORDS).lower()
    if clean_window in ["округ", "жк", "район", "районе", "метро", "м"]:
        return None

    bounds_stopwords = STOP_WORDS - LOCATION_MARKERS
    first_w = window_strings[0].lower()
    last_w = window_strings[-1].lower()
    if first_w in bounds_stopwords or last_w in bounds_stopwords:
        return None

    window_text = " ".join(window_strings)
    trigger_type, trigger_start = get_trigger_type(text_before)

    triggered_types = set()
    if trigger_type:
        triggered_types.add(trigger_type)

    kw_partial = ["вид", "сануз", "пол", "балкон", "лоджи"]
    window_lower = window_text.lower()
    window_words_lower = [w.lower() for w in window_strings]

    if any(kw in window_words_lower for kw in ["округ", "округе"]):
        triggered_types.add("county")
    if "жк" in window_words_lower:
        triggered_types.add("complex")
    if any(kw in window_words_lower for kw in ["район", "районе"]):
        triggered_types.add("district")
    if any(kw in window_words_lower for kw in ["метро", "м"]):
        triggered_types.add("metro")
    if any(kw in window_lower for kw in kw_partial) or any(
        kw in text_before[-30:].lower() for kw in kw_partial
    ):
        triggered_types.add("options")
        triggered_types.add("option_groups")

    query_norm = normalize(window_text)
    valid_choices = choices
    choice_strings = [c[0] for c in valid_choices]
    if not choice_strings:
        return None

    res = process.extract(query_norm, choice_strings, scorer=fuzz.WRatio, limit=100)

    good_res = []
    for r in res:
        matched_str = choice_strings[r[2]]
        _, etype, entry = valid_choices[r[2]]
        is_triggered = etype in triggered_types

        # Короткие записи справочника (аббревиатуры округов и т.п.) — точное
        # совпадение вместо порога, см. комментарий у SHORT_ENTITY_EXACT_MAX_LEN.
        if len(matched_str.replace(" ", "")) <= SHORT_ENTITY_EXACT_MAX_LEN:
            if query_norm == matched_str:
                good_res.append(r)
            continue

        item_threshold = TRIGGERED_SCORE_THRESHOLD if is_triggered else SCORE_THRESHOLD
        if r[1] < item_threshold:
            continue

        if is_triggered:
            clean_wratio = fuzz.WRatio(clean_window, matched_str)
            if clean_wratio < TRIGGERED_SCORE_THRESHOLD:
                continue

        # Однословные окна ("санузлом", "видом") под "options"/"option_groups"
        # дополнительно проверяются строго, даже если сработал подстроковый
        # kw_partial-триггер («сануз»/«вид»/«пол»/«балкон»/«лоджи» — часть
        # слова, а не явный анкерный маркер вроде "жк "/"метро "/"район ").
        # WRatio учитывает partial_ratio и завышает оценку, когда короткое
        # слово — просто подстрока куда более длинного многословного алиаса
        # («санузлом» ~ «несколько санузлов» = 78.75 WRatio, но всего 53.8
        # QRatio — семантически это НЕ совпадение: «отдельный санузел» ложно
        # матчился на «Два и более санузла», см. CLAUDE.md). Двусловные+
        # триггернутые окна («сквозным санузлом» ~ «сквозной санузел», «двумя
        # санузлами» ~ «2 санузла») не задеты — там сравниваются строки
        # сопоставимой длины, ложного «раздувания» partial_ratio нет.
        # Ограничено только options/option_groups: для metro/district/county/
        # complex однословные окна — штатный кейс явного анкерного триггера
        # ("ЖК X", "у метро X") на первом слове многословного имени сущности
        # (следующее окно уже целиком ловит полное имя, а это — промежуточный
        # кандидат); принудительная строгая проверка там убирала бы истинную
        # верхнюю оценку окна и обнажала слабый ЧУЖОЙ тип-кандидат на том же
        # спане (напр. "Волжский" в "ЖК Волжский парк" — совпадение с районом
        # «Всеволожский» при WRatio=QRatio=80.0).
        single_token_option_window = len(window_tokens) == 1 and etype in (
            "options",
            "option_groups",
        )
        if (is_synthetic or not is_triggered or single_token_option_window) and fuzz.QRatio(
            query_norm, matched_str
        ) < STRICT_QRATIO_THRESHOLD:
            continue

        good_res.append(r)

    if not good_res:
        return None

    best_score = good_res[0][1]
    top_matches = [r for r in good_res if best_score - r[1] < 5.0]

    matched_entities_info = []
    for matched_str, score, idx in top_matches:
        q_ratio = fuzz.QRatio(query_norm, matched_str)
        adjusted_base_score = score + (q_ratio / 1000.0)
        _, etype, entry = valid_choices[idx]
        matched_entities_info.append((adjusted_base_score, etype, entry))

    seen = set()
    unique_entities = []
    for score, etype, entry in matched_entities_info:
        if (etype, entry.name) not in seen:
            seen.add((etype, entry.name))
            adj_score = _adjust_score(score, etype, window_text, text_before, entry)
            unique_entities.append((adj_score, score, etype, entry))

    if not unique_entities:
        return None

    unique_entities.sort(key=lambda x: x[0], reverse=True)
    best_adj_score = unique_entities[0][0]
    best_entry_name = unique_entities[0][3].name

    unique_entities = [
        x for x in unique_entities if best_adj_score - x[0] < 5.0 or x[3].name == best_entry_name
    ]

    final_unique = [(orig_score, etyp, ent) for adj, orig_score, etyp, ent in unique_entities]

    actual_start_idx = start_idx
    if trigger_type and trigger_start is not None:
        actual_start_idx = trigger_start

    return {
        "span": (actual_start_idx, end_idx),
        "text": window_text,
        "matches": final_unique,
        "best_score": best_adj_score,
        "window_size": len(window_tokens),
        # Типоспецифичный контекст для разрешения конфликта одноимённых
        # сущностей разных типов в _resolve_candidates (Milestone AI-20,
        # Фикс 5): триггер из текста ПЕРЕД окном («у метро », «в районе»)
        # либо, если его нет, ЕДИНСТВЕННОЕ слово-носитель локации внутри
        # самого окна («метро Коммунарка» — окно содержит «метро»).
        "trigger_type": trigger_type or _sole_location_kw_type(triggered_types),
    }


def _resolve_candidates(candidates):
    def _is_overlap(span1: Span, span2: Span) -> bool:
        return max(span1[0], span2[0]) < min(span1[1], span2[1])

    candidates.sort(key=lambda c: (c["best_score"], c["window_size"]), reverse=True)
    final_matches: list[EntityMatch] = []
    warnings: list[str] = []
    used_spans_with_type = []

    for c in candidates:
        best_matches = c["matches"]
        top_match = best_matches[0]
        top_name = top_match[2].name

        # Одноимённые сущности РАЗНЫХ типов («Коммунарка» — и метро, и район)
        # раньше молча добавлялись обе: ambiguity-warning ниже завязан на
        # разные entry.name и для них не срабатывал (Milestone AI-20, Фикс 5).
        # Разрешение: типоспецифичный триггер контекста («у метро …»,
        # «в районе …») однозначно выбирает тип; без триггера берём лучший по
        # score тип и даём явный warning вместо тихого добавления обоих.
        same_name = [m for m in best_matches if m[2].name == top_name]
        same_name_ambiguity: list[tuple[float, str, RefEntry]] = []
        if len({m[1] for m in same_name}) > 1:
            trigger = c.get("trigger_type")
            if trigger and any(m[1] == trigger for m in same_name):
                same_name = [m for m in same_name if m[1] == trigger]
            else:
                chosen_type = same_name[0][1]
                same_name_ambiguity = [m for m in same_name if m[1] != chosen_type]
                same_name = [m for m in same_name if m[1] == chosen_type]

        added_any = False
        for score, etype, entry in same_name:
            if any(
                _is_overlap(c["span"], used_span) and used_type == etype
                for used_span, used_type in used_spans_with_type
            ):
                continue

            used_spans_with_type.append((c["span"], etype))
            final_matches.append(
                EntityMatch(
                    type=etype,
                    entity=MatchedEntity(name=entry.name, slug=entry.slug, id=entry.id),
                    score=score,
                    span=c["span"],
                )
            )
            added_any = True

        if added_any:
            type_names = {
                "metro": "метро",
                "county": "округ",
                "district": "район",
                "complex": "ЖК",
                "options": "опция",
                "option_groups": "группа опций",
            }
            other_matches = [m for m in best_matches if m[2].name != top_name]
            if other_matches:
                chosen_types = [type_names.get(m[1], m[1]) for m in same_name]
                chosen_types_str = " и ".join(chosen_types)
                alt_names = [f"{m[2].name} ({type_names.get(m[1], m[1])})" for m in other_matches]
                warnings.append(
                    f"Неоднозначность для «{c['text']}»: выбрано {top_name} ({chosen_types_str}), "
                    f"возможные варианты: {', '.join(alt_names)}"
                )
            if same_name_ambiguity:
                chosen_types_str = " и ".join(type_names.get(m[1], m[1]) for m in same_name)
                alt_types = ", ".join(
                    f"{top_name} ({type_names.get(m[1], m[1])})" for m in same_name_ambiguity
                )
                warnings.append(
                    f"Неоднозначность для «{c['text']}»: выбрано {top_name} "
                    f"({chosen_types_str}), возможные варианты: {alt_types}"
                )

    return final_matches, warnings


def match_entities(text: str) -> tuple[list[EntityMatch], list[str]]:
    choices = build_choices()

    tokens_info = []
    for m in re.finditer(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", text):
        tokens_info.append((m.group(), m.start(), m.end()))

    window_specs = _build_window_specs(tokens_info, text)

    candidates = []
    for window_tokens, text_before, start_idx, end_idx, is_synthetic in window_specs:
        candidate = _score_window(
            window_tokens, text_before, start_idx, end_idx, is_synthetic, text, choices
        )
        if candidate:
            candidates.append(candidate)

    return _resolve_candidates(candidates)
