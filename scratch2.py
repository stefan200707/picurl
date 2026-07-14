from app.parsing.entity_match import match_entities
text = "хочу двушку у метро Варшавская, до 15 млн, с отделкой, 15 минут до метро пешком, с выходом на солнечную сторону, раздельным санузлом"
matches, warnings = match_entities(text)
print("Matches:", matches)
print("Warnings:", warnings)
