"""Sliding-window + rapidfuzz matching of entities against reference data.

TODO(prompt 05): implement fuzzy entity matching.
"""

import re
from typing import NamedTuple

from rapidfuzz import fuzz, process

from app.parsing.rules import Span
from app.parsing.schema import MatchedEntity
from app.reference.loader import RefEntry, load_all, normalize

SCORE_THRESHOLD = 85.0
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
    "куплю",
    "мне",
    "нужна",
    "пожалуйста",
    "подскажите",
    "а",
    "но",
    "же",
    "только",
}


def build_choices() -> list[tuple[str, str, RefEntry]]:
    data = load_all()
    choices = []
    for etype, entries in [
        ("metro", data.metro),
        ("county", data.counties),
        ("district", data.districts),
        ("complex", data.complexes),
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


def match_entities(text: str) -> tuple[list[EntityMatch], list[str]]:
    choices = build_choices()

    tokens_info = []
    for m in re.finditer(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", text):
        tokens_info.append((m.group(), m.start(), m.end()))

    candidates = []

    for n in range(1, 4):
        for i in range(len(tokens_info) - n + 1):
            window_tokens = tokens_info[i : i + n]
            window_strings = [t[0] for t in window_tokens]

            if _is_stop_word_window(window_strings):
                continue

            start_idx = window_tokens[0][1]
            end_idx = window_tokens[-1][2]
            window_text = text[start_idx:end_idx]
            text_before = text[:start_idx]

            trigger_type, trigger_start = get_trigger_type(text_before)
            has_capital = any(w[0].isupper() for w in window_strings)

            if has_capital or trigger_type:
                query_norm = normalize(window_text)
                threshold = TRIGGERED_SCORE_THRESHOLD if trigger_type else SCORE_THRESHOLD

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
                            unique_entities.append((score, etype, entry))

                    actual_start_idx = start_idx
                    if trigger_type and trigger_start is not None:
                        actual_start_idx = trigger_start

                    candidates.append(
                        {
                            "span": (actual_start_idx, end_idx),
                            "text": window_text,
                            "matches": unique_entities,
                            "best_score": best_score,
                            "window_size": n,
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
