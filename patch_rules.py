import re

with open("app/parsing/rules.py", "r") as f:
    content = f.read()

old_finish_true = r"""_FINISH_TRUE = re.compile(
    r"\bс\s+(?:отделкой|ремонтом)\b|\b(?:пред)?чистов\w+(?:\s+отделк\w+)?"
    r"|\bготов\w+\s+отделк\w+|\bпод\s+ключ\b"
)"""

new_finish_true = r"""_FINISH_TRUE = re.compile(
    r"\bс\s+(?:отделкой|ремонтом)\b|\b(?:пред)?чистов\w+(?:\s+отделк\w+)?"
    r"|\bготов\w+\s+отделк\w+|\bпод\s+ключ\b|\bотделк\w+\b|\bремонт\w+\b"
)"""

content = content.replace(old_finish_true, new_finish_true)

old_extract = r"""    for match in _FINISH_TRUE.finditer(norm):
        candidates.append((match.start(), True, match.span()))
    if not candidates:
        return None, []
    # При противоречии побеждает последнее упоминание
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1], sorted(span for _, _, span in candidates)"""

new_extract = r"""    for match in _FINISH_TRUE.finditer(norm):
        is_inside_false = False
        for c in candidates:
            if c[1] is False and c[2][0] <= match.start() and c[2][1] >= match.end():
                is_inside_false = True
                break
        if not is_inside_false:
            candidates.append((match.start(), True, match.span()))
    if not candidates:
        return None, []
    
    # Сначала сортируем по старту, при равном старте предпочтение более длинному матчу
    candidates.sort(key=lambda item: (item[0], item[2][1] - item[2][0]))
    
    # При противоречии побеждает последнее упоминание, 
    # но только если они не перекрываются (иначе более длинный побеждает)
    # Удаляем полностью поглощенные
    filtered = []
    for c in candidates:
        if not filtered:
            filtered.append(c)
        else:
            prev = filtered[-1]
            if prev[2][0] <= c[2][0] and prev[2][1] >= c[2][1]:
                continue # c is completely inside prev, ignore
            if c[2][0] <= prev[2][0] and c[2][1] >= prev[2][1]:
                filtered[-1] = c # prev is completely inside c, replace
            else:
                filtered.append(c)
    
    return filtered[-1][1], sorted(span for _, _, span in filtered)"""

content = content.replace(old_extract, new_extract)

with open("app/parsing/rules.py", "w") as f:
    f.write(content)
