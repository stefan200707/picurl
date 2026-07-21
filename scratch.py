from app.parsing.parser import parse

text = "хочу двушку которая находится максимально близко к центру"
criteria, warnings = parse(text)
print("Criteria center_requested:", criteria.center_requested)
print("Warnings:", warnings)
