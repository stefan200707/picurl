import re

with open("app/parsing/entity_match.py", "r") as f:
    content = f.read()

old_res = """        res = process.extract(query_norm, choice_strings, scorer=fuzz.WRatio, limit=3)

        good_res = [r for r in res if r[1] >= threshold]
        if good_res:
            best_score = good_res[0][1]"""

new_res = """        res = process.extract(query_norm, choice_strings, scorer=fuzz.WRatio, limit=3)

        good_res = [r for r in res if r[1] >= threshold]
        if good_res and not (trigger_type or has_keyword or has_capital):
            # For purely lowercase untriggered windows, require stricter match (QRatio)
            # to avoid WRatio partial match false positives like "на востоке" in "выходом на крышу"
            filtered_res = []
            for r in good_res:
                matched_str = choice_strings[r[2]]
                if fuzz.QRatio(query_norm, matched_str) >= 80.0:
                    filtered_res.append(r)
            good_res = filtered_res

        if good_res:
            best_score = good_res[0][1]"""

content = content.replace(old_res, new_res)

with open("app/parsing/entity_match.py", "w") as f:
    f.write(content)
