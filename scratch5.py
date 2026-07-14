import re
_ROOMS_WORD_RANGES = re.compile(r"\bот\s+(одн\w+|двух|трех|четырех|пяти)\s+комнат\w*")
match = _ROOMS_WORD_RANGES.search("от двух комнат")
print(match)
