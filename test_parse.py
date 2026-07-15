import asyncio
from app.parsing.parser import parse

res, warnings = parse("хочу двушку у метро, до 15 млн, с отделкой, пик бунинские луга")
print("CRITERIA:", res.to_public_dict())
print("WARNINGS:", warnings)
