import asyncio
import json
import sys
from pathlib import Path
from app.reference.loader import load_complexes
from app.geo.poi import POICategory, fetch_poi

CACHE_FILE = Path(__file__).parent.parent / "reference" / "poi_cache.json"

async def refresh_all_pois():
    complexes = load_complexes()
    
    if CACHE_FILE.exists():
        cache = json.loads(CACHE_FILE.read_text("utf-8"))
    else:
        cache = {}
        
    for complex in complexes:
        if not complex.lat or not complex.lon or not complex.slug:
            continue
            
        if complex.slug not in cache:
            cache[complex.slug] = {}
            
        for category in POICategory:
            if category == POICategory.OTHER:
                continue
                
            if category.value in cache[complex.slug]:
                continue
                
            print(f"Fetching {category.value} for {complex.name}...")
            try:
                # Use a large radius like 2000m to cache max potential range
                res = await fetch_poi(complex.lat, complex.lon, category, 2000)
                cache[complex.slug][category.value] = res.model_dump()
                await asyncio.sleep(1)
            except Exception as e:
                print(f"Failed to fetch for {complex.name} / {category}: {e}")
                
        CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False) + "\n", "utf-8")
        
    print("Refresh POI completed.")

def main():
    asyncio.run(refresh_all_pois())
    return 0

if __name__ == "__main__":
    sys.exit(main())
