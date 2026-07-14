import urllib.request
import json
import os

url = "https://api.hh.ru/metro/1"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
html = urllib.request.urlopen(req).read().decode('utf-8')
data = json.loads(html)

stations = set()
for line in data['lines']:
    for station in line['stations']:
        stations.add(station['name'])

old_data = []
if os.path.exists("app/reference/metro.json"):
    with open("app/reference/metro.json", "r", encoding="utf-8") as f:
        old_data = json.load(f)

old_dict = {item['name']: item for item in old_data}
for s in stations:
    if s not in ("Аэропорт", "Бабушкинская"):
        if s not in old_dict:
            old_dict[s] = {"name": s}

if "Варшавская" not in old_dict:
    old_dict["Варшавская"] = {"name": "Варшавская"}

out = sorted(old_dict.values(), key=lambda x: x['name'])
print(f"Total stations now: {len(out)}")
with open("app/reference/metro.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
