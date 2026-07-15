from rapidfuzz import process, fuzz
print(process.extract("выходом на крышу", ["ВАО", "ЗАО", "САО"], scorer=fuzz.WRatio))
