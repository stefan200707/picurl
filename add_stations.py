import json
import urllib.request

req = urllib.request.Request("https://api.hh.ru/metro/1", headers={"User-Agent": "Mozilla/5.0"})
data = urllib.request.urlopen(req).read()
j = json.loads(data)

stations = set()
for line in j["lines"]:
    for station in line["stations"]:
        stations.add(station["name"])

with open('app/reference/metro.json', 'r') as f:
    existing = json.load(f)

existing_names = {x['name'] for x in existing}
for s in sorted(stations):
    if s not in existing_names:
        existing.append({"name": s})

existing.sort(key=lambda x: x["name"])

with open('app/reference/metro.json', 'w', encoding='utf-8') as f:
    json.dump(existing, f, ensure_ascii=False, indent=2)

