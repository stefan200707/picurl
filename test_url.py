from app.parsing.parser import parse
from app.pik.url_builder import build_url

res, warnings = parse("хочу двушку у метро, до 15 млн, с отделкой, пик бунинские луга")
print("URL:", build_url(res))
