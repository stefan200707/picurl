from app.parsing.parser import parse
text = "хочу двушку, отделка"
print(parse(text).warnings)
