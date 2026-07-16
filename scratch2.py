import re
from rapidfuzz import fuzz, process
from app.parsing.entity_match import build_choices, normalize, _adjust_score
choices = build_choices()
query = normalize("район Солнцево")
res = process.extract(query, [c[0] for c in choices], scorer=fuzz.WRatio, limit=10)
for r in res:
    matched_str = r[0]
    score = r[1]
    idx = r[2]
    _, etype, entry = choices[idx]
    
    q_ratio = fuzz.QRatio(query, matched_str)
    adj_base = score + (q_ratio / 1000.0)
    
    final_adj = _adjust_score(adj_base, etype, "район Солнцево", "", entry)
    print(f"Name: {entry.name}, Type: {etype}, OrigWRatio: {score}, AdjBase: {adj_base}, FinalAdj: {final_adj}")
