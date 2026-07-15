from rapidfuzz import process, fuzz
from app.reference.loader import load_all, normalize

choices = []
data = load_all()
for entry in data.counties:
    if entry.name == "ВАО":
        choices.append(normalize(entry.name))
        for a in entry.aliases:
            choices.append(normalize(a))

print("Choices for ВАО:", choices)
print(process.extract(normalize("выходом на крышу"), choices, scorer=fuzz.WRatio))
