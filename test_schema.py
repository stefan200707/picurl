from app.reference.loader import load_all
data = load_all()
m = [x for x in data.metro if x.name in ("Строгино", "Митино", "Мякинино")]
d = [x for x in data.districts if x.name in ("Строгино", "Митино", "Мякинино")]
c = [x for x in data.counties if x.name in ("ЗАО", "СЗАО")]
print("Metro:", len(m))
print("Districts:", len(d))
print("Counties:", len(c))
