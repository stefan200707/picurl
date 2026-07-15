import asyncio
from app.parsing.entity_match import match_entities

text = "Строгино Митино Мякинино ЗАО СЗАО"
matches, warnings = match_entities(text)
for m in matches:
    print(m)
print("Warnings:", warnings)
