from app.parsing.entity_match import match_entities
matches, warnings = match_entities("ищу квартиру: Западный")
print("MATCHES:", matches)
print("WARNINGS:", warnings)
