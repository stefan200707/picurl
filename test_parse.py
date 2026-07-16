import asyncio
from app.parsing.parser import parse

text = "нужна студия или однушка до 12 млн, с площадью от 27 кв, до метро не более 12 минут пешком, заселение до 2030 года и выше 7 этажа"
c, w = parse(text)
print("Criteria:")
print(c.model_dump(exclude_none=True, exclude_unset=True))
print("Warnings:")
print(w)
