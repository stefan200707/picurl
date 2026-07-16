import os

with open("tests/integration/test_build_url_e2e.py", "r") as f:
    c = f.read()
c = c.replace("from app.main import app, get_http_client", "from app.main import app\nfrom app.api.endpoints import get_http_client")
with open("tests/integration/test_build_url_e2e.py", "w") as f:
    f.write(c)

with open("tests/parsing/test_schema.py", "r") as f:
    c = f.read()
c = c.replace("from app.main import BuildUrlRequest, BuildUrlResponse", "from app.api.schemas import BuildUrlRequest, BuildUrlResponse")
with open("tests/parsing/test_schema.py", "w") as f:
    f.write(c)

with open("app/parsing/rules/__init__.py", "r") as f:
    c = f.read()
c = c.replace("from .price import extract_price", "from .price import extract_price, PriceFacts")
c = c.replace("from .area import extract_area", "from .area import extract_area, AreaFacts")
c = c.replace("from .time import extract_time_to_metro", "from .time import extract_time_to_metro, TimeFacts")
c = c.replace("from .floor import extract_floor", "from .floor import extract_floor, FloorFacts")

with open("app/parsing/rules/__init__.py", "w") as f:
    f.write(c)
