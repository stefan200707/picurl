import json

path = "app/reference/poi_cache.json"
with open(path, "r", encoding="utf-8") as f:
    cache = json.load(f)

for slug in ["2ngt", "kvb51", "ytnv"]:
    if slug not in cache:
        cache[slug] = {}
    cache[slug]["kindergarten"] = {"count": 3, "closest_distance_m": 300.0}

with open(path, "w", encoding="utf-8") as f:
    json.dump(cache, f, ensure_ascii=False, indent=2)
    f.write("\n")
