"""Sliding-window + rapidfuzz matching of entities against reference data.

TODO(prompt 05): implement fuzzy entity matching.
"""

import re
from typing import NamedTuple

from rapidfuzz import fuzz, process

from app.parsing.rules import Span
from app.parsing.schema import MatchedEntity
from app.reference.loader import RefEntry, load_all, normalize

SCORE_THRESHOLD = 80.0
TRIGGERED_SCORE_THRESHOLD = 75.0


class EntityMatch(NamedTuple):
    type: str  # "metro", "county", "district", "complex"
    entity: MatchedEntity
    score: float
    span: Span


TRIGGERS = [
    # (pattern, type)
    (r"(?i)\bу\s+метро\s+$", "metro"),
    (r"(?i)\bна\s+метро\s+$", "metro"),
    (r"(?i)\bрядом\s+с\s+метро\s+$", "metro"),
    (r"(?i)\bм\.\s+$", "metro"),
    (r"(?i)\bм\s+$", "metro"),
    (r"(?i)\bв\s+районе\s+$", "district"),
    (r"(?i)\bокруг\s+$", "county"),
    (r"(?i)\bв\s+юзао\s+$", "county"),
    (r"(?i)\bв\s+жк\s+$", "complex"),
    (r"(?i)\bжк\s+$", "complex"),
]

COMPILED_TRIGGERS = [(re.compile(pat), ttype) for pat, ttype in TRIGGERS]

STOP_WORDS = {
    "в",
    "на",
    "у",
    "с",
    "по",
    "и",
    "или",
    "для",
    "к",
    "от",
    "до",
    "за",
    "около",
    "рядом",
    "хочу",
    "ищу",
    "квартиру",
    "квартиры",
    "квартира",
    "куплю",
    "мне",
    "нужна",
    "пожалуйста",
    "подскажите",
    "а",
    "но",
    "же",
    "только",
    "районе",
    "округе",
}


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
    for pat, ttype in COMPILED_TRIGGERS:
        m = pat.search(text_before)
        if m:
            return ttype, m.start()
    return None, None


def _is_stop_word_window(tokens: list[str]) -> bool:
    return all(t.lower() in STOP_WORDS for t in tokens)


def _adjust_score(
    score: float, etype: str, window_text: str, text_before: str, entry: RefEntry
) -> float:
    before_tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", text_before)
    before_tokens = [t.lower() for t in before_tokens]
    window_tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", window_text)
    window_tokens = [t.lower() for t in window_tokens]

    last_3_before = before_tokens[-3:] if before_tokens else []
    entry_names = [entry.name.lower()] + [a.lower() for a in entry.aliases]

    def _is_marker_context(markers: list[str]) -> bool:
        for m in markers:
            if m in last_3_before:
                return True
            if m in window_tokens:
                m_in_name = any(re.search(rf"\b{re.escape(m)}\b", name) for name in entry_names)
                if not m_in_name:
                    return True
        return False

    has_jk = _is_marker_context(["жк", "жилой комплекс"])
    has_district = _is_marker_context(["район", "районе"])
    has_county = _is_marker_context(["округ", "округе"])
    has_metro = _is_marker_context(["м", "метро"]) or "м." in last_3_before

    new_score = score
    if has_jk and etype == "complex":
        new_score += 10.0
    if has_district and etype == "district":
        new_score += 10.0
    if has_county and etype == "county":
        new_score += 10.0
    if has_metro and etype == "metro":
        new_score += 10.0

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
                new_score += 5.0
            elif etype == "complex":
                new_score -= 5.0

    if prep_v:
        first_in_name = first_word_window == "в" or any(
            first_word_window in name for name in entry_names
        )
        if first_in_name and etype == "complex":
            new_score += 5.0

    hierarchy = {
        "county": 0.5,
        "district": 0.4,
        "metro": 0.3,
        "complex": 0.2,
        "options": 0.1,
        "option_groups": 0.0,
    }
    new_score += hierarchy.get(etype, 0.0)

    return new_score


def match_entities(text: str) -> tuple[list[EntityMatch], list[str]]:
    choices = build_choices()

    tokens_info = []
    for m in re.finditer(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", text):
        tokens_info.append((m.group(), m.start(), m.end()))

    candidates = []

    window_specs = []

    for n in range(1, 4):
        for i in range(len(tokens_info) - n + 1):
            window_tokens = tokens_info[i : i + n]
            start_idx = window_tokens[0][1]
            end_idx = window_tokens[-1][2]
            text_before = text[:start_idx]
            window_specs.append((window_tokens, text_before, start_idx, end_idx))

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
                    window_specs.append((synthetic_tokens, t_before, s_start, s_end))

    for window_tokens, text_before, start_idx, end_idx in window_specs:
        window_strings = [t[0] for t in window_tokens]

        if _is_stop_word_window(window_strings):
            continue

        clean_window = " ".join(w for w in window_strings if w.lower() not in STOP_WORDS).lower()
        if clean_window in ["округ", "жк", "район", "районе", "метро", "м"]:
            continue

        # Отсекаем окна, которые начинаются или заканчиваются на висячий союз/предлог
        # (если они часть устойчивого названия, они останутся внутри окна)
        if window_strings[0].lower() in STOP_WORDS or window_strings[-1].lower() in STOP_WORDS:
            continue

        # Формируем текст окна из очищенных токенов, чтобы знаки препинания не прилипали
        window_text = " ".join(window_strings)

        trigger_type, trigger_start = get_trigger_type(text_before)
        has_capital = any(w[0].isupper() for w in window_strings)

        kw_exact = {"округ", "жк", "район", "метро", "м"}
        kw_partial = ["вид", "сануз", "пол", "балкон", "лоджи"]
        window_lower = window_text.lower()
        window_words_lower = [w.lower() for w in window_strings]

        has_keyword = any(kw in window_words_lower for kw in kw_exact) or any(
            kw in window_lower for kw in kw_partial
        )

        if has_capital or trigger_type or has_keyword:
            query_norm = normalize(window_text)
            if trigger_type or has_keyword:
                threshold = TRIGGERED_SCORE_THRESHOLD
            else:
                threshold = SCORE_THRESHOLD

            valid_choices = choices
            if trigger_type:
                valid_choices = [c for c in choices if c[1] == trigger_type]

            choice_strings = [c[0] for c in valid_choices]
            if not choice_strings:
                continue

            res = process.extract(query_norm, choice_strings, scorer=fuzz.WRatio, limit=3)

            good_res = [r for r in res if r[1] >= threshold]
            if good_res:
                best_score = good_res[0][1]
                top_matches = [r for r in good_res if best_score - r[1] < 5.0]

                matched_entities_info = []
                for _matched_str, score, idx in top_matches:
                    _, etype, entry = valid_choices[idx]
                    matched_entities_info.append((score, etype, entry))

                seen = set()
                unique_entities = []
                for score, etype, entry in matched_entities_info:
                    if (etype, entry.name) not in seen:
                        seen.add((etype, entry.name))
                        adj_score = _adjust_score(score, etype, window_text, text_before, entry)
                        unique_entities.append((adj_score, score, etype, entry))

                if not unique_entities:
                    continue

                unique_entities.sort(key=lambda x: x[0], reverse=True)
                best_adj_score = unique_entities[0][0]

                # Map back to original structure for candidates
                final_unique = [
                    (orig_score, etyp, ent) for adj, orig_score, etyp, ent in unique_entities
                ]

                actual_start_idx = start_idx
                if trigger_type and trigger_start is not None:
                    actual_start_idx = trigger_start

                candidates.append(
                    {
                        "span": (actual_start_idx, end_idx),
                        "text": window_text,
                        "matches": final_unique,
                        "best_score": best_adj_score,
                        "window_size": len(window_tokens),
                    }
                )

    def _is_overlap(span1: Span, span2: Span) -> bool:
        return max(span1[0], span2[0]) < min(span1[1], span2[1])

    candidates.sort(key=lambda c: (c["best_score"], c["window_size"]), reverse=True)

    final_matches: list[EntityMatch] = []
    warnings: list[str] = []

    used_spans = []
    for c in candidates:
        if any(_is_overlap(c["span"], used) for used in used_spans):
            continue

        used_spans.append(c["span"])

        best_matches = c["matches"]
        top_match = best_matches[0]
        score, etype, entry = top_match

        final_matches.append(
            EntityMatch(
                type=etype,
                entity=MatchedEntity(name=entry.name, slug=entry.slug, id=entry.id),
                score=score,
                span=c["span"],
            )
        )

        if len(best_matches) > 1:
            alt_names = [m[2].name for m in best_matches[1:]]
            warnings.append(
                f"Неоднозначность для «{c['text']}»: выбрано {entry.name}, "
                f"возможные варианты: {', '.join(alt_names)}"
            )

    return final_matches, warnings
