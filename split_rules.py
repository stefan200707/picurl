import os
import re

with open("app/parsing/rules.py", "r") as f:
    content = f.read()

content = content.replace('cleaned = raw.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")', 
                          'cleaned = re.sub(r"[\\s\\xa0\\u202f]", "", raw).replace(",", ".")')

os.makedirs("app/parsing/rules_new", exist_ok=True)

parts = re.split(r'# -{3,}\n# (.*?)\n# -{3,}\n', content)

header = parts[0]
sections = {}
for i in range(1, len(parts), 2):
    sections[parts[i].strip()] = parts[i+1]

with open("app/parsing/rules_new/core.py", "w") as f:
    f.write(header)
    f.write("# ---------------------------------------------------------------------------\n")
    f.write("# Нормализация и общие помощники\n")
    f.write("# ---------------------------------------------------------------------------\n")
    f.write(sections["Нормализация и общие помощники"])

common_imports = """
import re
from typing import NamedTuple
from app.parsing.schema import Criteria, Finish, HousingType, Rooms, Sort
from .core import Span, _overlaps, _iter_free, _NUM, _to_number, _normalize
"""

mapping = {
    "Комнатность": "rooms.py",
    "Цена": "price.py",
    "Площадь (общая и кухни)": "area.py",
    "Время до метро": "time.py",
    "Этаж": "floor.py",
    "Отделка и заселение": "finish.py",
    "Год заселения": "misc.py",
    "Сортировка": "misc.py",
    "Выгодные предложения (requiredTags)": "misc.py",
    "Прочее: тип жилья, доступность, неподдерживаемое": "misc.py",
    "Агрегат: все правила разом": "__init__.py"
}

files_content = {
    "rooms.py": common_imports + "\n",
    "price.py": common_imports + "\n",
    "area.py": common_imports + "\n",
    "time.py": common_imports + "\nfrom yargy import Parser, or_, rule\nfrom yargy.interpretation import fact\nfrom yargy.pipelines import morph_pipeline\nfrom yargy.predicates import dictionary\nfrom yargy.predicates import type as yargy_type\n",
    "floor.py": common_imports + "\n",
    "finish.py": common_imports + "\n",
    "misc.py": common_imports + "\n"
}

for title, text in sections.items():
    if title == "Нормализация и общие помощники":
        continue
    
    file_name = mapping.get(title)
    if not file_name:
        print(f"Unknown section: {title}")
        continue
        
    if file_name == "__init__.py":
        with open("app/parsing/rules_new/__init__.py", "w") as f:
            f.write(common_imports.replace("import re\n", "import re\nfrom .core import Span, _normalize\nfrom .rooms import extract_rooms\nfrom .price import extract_price\nfrom .area import extract_area\nfrom .time import extract_time_to_metro\nfrom .floor import extract_floor\nfrom .finish import extract_finish, extract_ready\nfrom .misc import extract_settlement_year, extract_sort, extract_required_tags, extract_housing_type, extract_only_available, extract_unsupported, extract_fallback_metro\nfrom app.parsing.schema import Criteria\n"))
            f.write(text)
    else:
        files_content[file_name] += f"# --- {title} ---\n" + text

for fname, fcontent in files_content.items():
    with open(f"app/parsing/rules_new/{fname}", "w") as f:
        f.write(fcontent)
        
