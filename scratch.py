import asyncio
from app.parsing.parser import parse
from app.pik.url_builder import build_url

text = "нужна квартира с видом на парк, западный округ, предчистовая отделка, в районе 9-16 этажей, два и более санузла, с тёплым полом, от двух комнат, до метро менее 15 минут"
criteria, warnings = parse(text)
url = build_url(criteria)

print("Criteria:", criteria)
print("Warnings:", warnings)
print("URL:", url)
