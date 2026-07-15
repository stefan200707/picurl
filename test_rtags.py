from app.parsing.rules import extract_required_tags, apply_rules
from app.parsing.schema import Criteria
from app.pik.url_builder import build_url

print(extract_required_tags("готовые квартиры"))
print(extract_required_tags("выгода до -15% до 15.07"))

c = Criteria(required_tags=["zos", "cashback"])
print(build_url(c))
