from app.parsing.parser import parse
from app.reference.loader import load_all

load_all()
res, warnings = parse("Ищем студию или однушку, бюджет до 8 млн, черновая отделка, хотя нет, лучше с отделкой под ключ, заселение в этом году, метро Ховрино, площадь 30-40 метров, подешевле, этаж 10-15.")
print(res)
print(warnings)
