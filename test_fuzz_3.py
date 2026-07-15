from rapidfuzz import fuzz
print("WRatio:", fuzz.WRatio("выходом на крышу", "вао"))
print("QRatio:", fuzz.QRatio("выходом на крышу", "вао"))
