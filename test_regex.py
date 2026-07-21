import re

PREFIX = r"(?:\bнов\w+\s+)?"
SUFFIX = r"(?:\s+(?:поблизости|неподалеку|неподалёку|рядом\s+с\s+ним|рядом|близко))?"

pat = re.compile(PREFIX + r"(?:\bсадик\w*|\bдетск\w+\s+сад\w*|\bдетсад\w*)" + SUFFIX)

tests = [
    "с новыми детсадами поблизости",
    "садик",
    "детский сад",
    "новые школы",
    "рядом детсады",
    "детсад рядом",
]
for t in tests:
    m = pat.search(t)
    if m:
        print(f"Matched '{t}':", m.group(0))
