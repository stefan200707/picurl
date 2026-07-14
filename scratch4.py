from app.parsing.parser import parse
text = "хочу двушку у метро Варшавская, до 15 млн, с отделкой, 15 минут до метро пешком, с выходом на солнечную сторону, раздельным санузлом"
res = parse(text)
import json
print(res.criteria.model_dump_json(indent=2))
print("Warnings:", res.warnings)
