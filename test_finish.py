import re

_FINISH_FALSE = re.compile(r"\bбез\s+(?:отделки|ремонта)\b|\bчернов\w+(?:\s+отделк\w+)?")
_FINISH_TRUE = re.compile(
    r"\bс\s+(?:отделкой|ремонтом)\b|\b(?:пред)?чистов\w+(?:\s+отделк\w+)?"
    r"|\bготов\w+\s+отделк\w+|\bпод\s+ключ\b|\bотделк\w+\b|\bремонт\w+\b"
)
_FINISH_PRED = re.compile(r"\bпредчистов\w+(?:\s+отделк\w+)?")
print(_FINISH_PRED.findall("отделка предчистовая"))
print(_FINISH_PRED.findall("предчистовая отделка"))
