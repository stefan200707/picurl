from app.parsing.rules import apply_rules
text = "хочу двушку у метро Варшавская, до 15 млн, с отделкой, 15 минут до метро пешком, с выходом на солнечную сторону, раздельным санузлом"
rules_outcome = apply_rules(text)
print("Consumed:", rules_outcome.consumed)
for span in rules_outcome.consumed:
    print(f"  {span} -> '{text[span[0]:span[1]]}'")
