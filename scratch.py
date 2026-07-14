import re
text = "хочу двушку у метро Варшавская"
tokens_info = []
for m in re.finditer(r"[A-Za-zА-Яа-яЁё0-9]+(?:-[A-Za-zА-Яа-яЁё0-9]+)*", text):
    tokens_info.append((m.group(), m.start(), m.end()))

for n in range(1, 4):
    for i in range(len(tokens_info) - n + 1):
        window_tokens = tokens_info[i : i + n]
        start_idx = window_tokens[0][1]
        text_before = text[:start_idx]
        print(f"n={n}, window={window_tokens[0][0]}, text_before='{text_before}'")
        m = re.search(r"(?i)\bу\s+метро\s+$", text_before)
        if m:
            print("MATCHED!")
