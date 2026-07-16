import re
from rapidfuzz import fuzz, process
from app.parsing.stopwords import STOP_WORDS
from app.reference.loader import load_all, normalize
from app.parsing.entity_match import get_trigger_type, build_choices, SCORE_THRESHOLD, TRIGGERED_SCORE_THRESHOLD, STRICT_QRATIO_THRESHOLD, _adjust_score

text = "ищу квартиру: Западный"

choices = build_choices()
print("Choices loaded")

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
    print(f"\nWindow: '{window_text}'")
    
    clean_window = " ".join(w for w in window_strings if w.lower() not in STOP_WORDS).lower()
    if clean_window in ["округ", "жк", "район", "районе", "метро", "м"]:
        print("Skipped due to clean_window check")
        continue

    if window_strings[0].lower() in STOP_WORDS or window_strings[-1].lower() in STOP_WORDS:
        print("Skipped due to stopword at bounds")
        continue

    query_norm = normalize(window_text)
    choice_strings = [c[0] for c in choices]
    
    res = process.extract(query_norm, choice_strings, scorer=fuzz.WRatio, limit=10)
    print("Top WRatio matches:", [(r[0], r[1]) for r in res])
    
    good_res = []
    for r in res:
        matched_str = choice_strings[r[2]]
        _, etype, entry = choices[r[2]]
        
        is_triggered = False # simplified
        item_threshold = SCORE_THRESHOLD
        
        if r[1] < item_threshold:
            continue
            
        if (is_synthetic or not is_triggered) and fuzz.QRatio(query_norm, matched_str) < STRICT_QRATIO_THRESHOLD:
            print(f"Skipped {matched_str} due to QRatio: {fuzz.QRatio(query_norm, matched_str)}")
            continue
            
        good_res.append(r)
        
    print("Good res:", [(choice_strings[r[2]], r[1]) for r in good_res])
