from app.parsing.parser import parse
from app.parsing.entity_match import match_entities

text = "хочу двушку у метро, до 15 млн, с отделкой, пик бунинские луга"
print("ENTITIES:", match_entities(text))
res, warnings = parse(text)
print("WARNINGS:", warnings)
