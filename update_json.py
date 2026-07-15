import json


def update_file(path, new_items):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    existing_slugs = {item.get("slug"): item for item in data if "slug" in item}

    for item in new_items:
        slug = item["slug"]
        if slug in existing_slugs:
            # Update existing
            existing_slugs[slug]["aliases"] = list(
                set(existing_slugs[slug].get("aliases", []) + item.get("aliases", []))
            )
        else:
            data.append(item)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


option_groups = [
    {
        "name": "Уникальная квартира",
        "slug": "unikalnayaKvartira",
        "aliases": ["уникальная квартира", "уникальная", "необычная квартира"],
    },
    {"name": "Гардеробная", "slug": "wardrobe", "aliases": ["гардеробная", "с гардеробной"]},
    {"name": "Постирочная", "slug": "laundryroom", "aliases": ["постирочная", "с постирочной"]},
    {
        "name": "Мастер-спальня",
        "slug": "masterbedroom",
        "aliases": ["мастер-спальня", "мастер спальня", "со своей ванной"],
    },
    {
        "name": "Разнесённые спальни",
        "slug": "spacedbedrooms",
        "aliases": ["разнесенные спальни", "разнесённые спальни", "изолированные спальни"],
    },
    {
        "name": "Два и более санузла",
        "slug": "manybathrooms",
        "aliases": ["два санузла", "несколько санузлов", "2 санузла"],
    },
    {
        "name": "Сквозной санузел",
        "slug": "throughbathroom",
        "aliases": ["сквозной санузел", "проходной санузел"],
    },
    {"name": "Терраса", "slug": "terrasa", "aliases": ["терраса", "с террасой"]},
    {
        "name": "Балкон",
        "slug": "balcony",
        "aliases": ["балкон", "с балконом", "лоджия", "с лоджией"],
    },
    {"name": "Большие окна", "slug": "bigwindows", "aliases": ["большие окна", "панорамные окна"]},
    {"name": "Окно в гардеробной", "slug": "windowdressing", "aliases": ["окно в гардеробной"]},
    {"name": "Угловые окна", "slug": "cornerwindows", "aliases": ["угловые окна", "угловое окно"]},
    {"name": "Эркер", "slug": "baywindow", "aliases": ["эркер", "с эркером"]},
    {
        "name": "Окно в ванной",
        "slug": "bathroomwindow",
        "aliases": ["окно в ванной", "с окном в ванной"],
    },
    {"name": "Окно в постирочной", "slug": "oknoVPostirochnoj", "aliases": ["окно в постирочной"]},
    {"name": "Видовая квартира", "slug": "view", "aliases": ["видовая квартира", "видовая"]},
    {"name": "Тёплый пол", "slug": "teplyPol", "aliases": ["тёплый пол", "с теплым полом"]},
    {
        "name": "Внутрипольные конвекторы",
        "slug": "trenchconvectors",
        "aliases": ["внутрипольные конвекторы", "конвекторы в полу"],
    },
]

options = [
    {
        "name": "Вид на школу",
        "slug": "vidNaShkolu",
        "aliases": ["вид на школу", "с видом на школу"],
    },
    {
        "name": "Вид на воду",
        "slug": "vidNaVodu",
        "aliases": ["вид на воду", "вид на реку", "с видом на воду"],
    },
    {"name": "Вид на город", "slug": "vidNaGorod", "aliases": ["вид на город", "с видом на город"]},
    {"name": "Вид во двор", "slug": "vidVoDvor", "aliases": ["вид во двор", "с видом во двор"]},
    {"name": "Вид на парк", "slug": "vidNaPark", "aliases": ["вид на парк", "с видом на парк"]},
    {
        "name": "Вид на дорогу",
        "slug": "vidNaDorogu",
        "aliases": ["вид на дорогу", "с видом на дорогу"],
    },
]

update_file("app/reference/option_groups.json", option_groups)
update_file("app/reference/options.json", options)
