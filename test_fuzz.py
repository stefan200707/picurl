from rapidfuzz import fuzz, process
from app.reference.loader import normalize

q1 = normalize("пик бунинские луга")
q2 = normalize("бунинские луга")
print("q1:", fuzz.WRatio(q1, "бунинские луга"))
print("q2:", fuzz.WRatio(q2, "бунинские луга"))
