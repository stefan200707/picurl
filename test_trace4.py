import re
from rapidfuzz import fuzz, process
from app.parsing.stopwords import STOP_WORDS, LOCATION_MARKERS
from app.reference.loader import load_all, normalize
from app.parsing.entity_match import get_trigger_type, build_choices, SCORE_THRESHOLD, TRIGGERED_SCORE_THRESHOLD, STRICT_QRATIO_THRESHOLD, _adjust_score

text = "хочу             у метро Аэропорт Внуково, до 15 млн, с отделкой, сначала дешевле"

choices = build_choices()
tokens_info = []
for m in re.finditer(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", text):
    tokens_info.append((m.group(), m.start(), m.end()))

window_specs = []
for n in range(1, 6):
    for i in range(len(tokens_info) - n + 1):
        window_tokens = tokens_info[i : i + n]
        start_idx = window_tokens[0][1]
        end_idx = window_tokens[-1][2]
        text_before = text[:start_idx]
        window_specs.append((window_tokens, text_before, start_idx, end_idx, False))

for window_tokens, text_before, start_idx, end_idx, is_synthetic in window_specs:
    window_strings = [t[0] for t in window_tokens]
    window_text = " ".join(window_strings)
    if window_text == "Аэропорт Внуково":
        print(f"\nWindow: '{window_text}'")
        bounds_stopwords = STOP_WORDS - LOCATION_MARKERS
        if window_strings[0].lower() in bounds_stopwords or window_strings[-1].lower() in bounds_stopwords:
            print("Skipped bounds_stopwords")
            continue
        
        query_norm = normalize(window_text)
        choice_strings = [c[0] for c in choices]
        res = process.extract(query_norm, choice_strings, scorer=fuzz.WRatio, limit=10)
        print("Top matches:", [(r[0], r[1]) for r in res])
        
        for r in res:
            matched_str = choice_strings[r[2]]
            _, etype, entry = choices[r[2]]
            print(f"Matched {matched_str} with score {r[1]}, entry={entry.name}")

