from app.parsing.entity_match import match_entities
text = "3к, от 70 м², кухня от 16 м², до 20 млн руб, чистовая отделка, этаж 5–10, не крайний, район Солнцево, метро Озерная, только готовые квартиры, без альтернативы, подешевле."
print(match_entities(text))
