import asyncio
import json
import sys
from pathlib import Path

from app.geo.poi import POICategory, fetch_poi
from app.reference.loader import load_complexes

CACHE_FILE = Path(__file__).parent.parent / "reference" / "poi_cache.json"


async def refresh_all_pois():
    complexes = load_complexes()

    cache = json.loads(CACHE_FILE.read_text("utf-8")) if CACHE_FILE.exists() else {}

    for block in complexes:
        if not block.lat or not block.lon or not block.slug:
            continue

        if block.slug not in cache:
            cache[block.slug] = {}

        for category in POICategory:
            if category == POICategory.OTHER:
                continue

            if category.value in cache[block.slug]:
                continue

            print(f"Fetching {category.value} for {block.name}...")
            try:
                # Use a large radius like 2000m to cache max potential range
                res = await fetch_poi(block.lat, block.lon, category, 2000)
                cache[block.slug][category.value] = res.model_dump()
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Failed to fetch for {block.name} / {category}: {e}")

        CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", "utf-8")

    print("Refresh POI completed.")


def main():
    asyncio.run(refresh_all_pois())
    return 0


if __name__ == "__main__":
    sys.exit(main())
