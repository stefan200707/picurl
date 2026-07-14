from app.parsing.parser import parse

text = "хочу квартиру в 15 минутах от метро, на Западе, на 9-16 этаже, с тёплым полом, с готовой отделкой, с видом на воду и город, двушка"
res, warns = parse(text)
print("Criteria:", res)
print("Warns:", warns)
