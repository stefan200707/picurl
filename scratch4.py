import asyncio
from app.parsing.parser import parse

text = "хочу квартиру с видом на парк, западный округ, предчистовая отделка, в районе 9-16 этажей, два и более санузла, с тёплым полом, от двух комнат, до метро менее 15 минут"
res = parse(text)
print("Criteria:", res.criteria)
print("Warnings:", res.warnings)
