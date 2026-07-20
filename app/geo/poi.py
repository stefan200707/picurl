import json
from enum import StrEnum
from pydantic import BaseModel
import httpx

class POICategory(StrEnum):
    SCHOOL = "school"
    KINDERGARTEN = "kindergarten"
    SHOP = "shop"
    PARKING = "parking"
    PARK_FOREST = "park_forest"
    OTHER = "other"

class POIResult(BaseModel):
    count: int
    closest_distance_m: float | None = None

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

def get_overpass_query(lat: float, lon: float, category: POICategory, radius_m: int) -> str:
    tags = {
        POICategory.SCHOOL: "amenity=school",
        POICategory.KINDERGARTEN: "amenity=kindergarten",
        POICategory.SHOP: "shop~'supermarket|convenience'",
        POICategory.PARKING: "amenity=parking",
    }
    
    if category == POICategory.PARK_FOREST:
        tag_query = f'nwr["leisure"="park"](around:{radius_m},{lat},{lon});nwr["natural"="wood"](around:{radius_m},{lat},{lon});nwr["landuse"="forest"](around:{radius_m},{lat},{lon});'
    elif category in tags:
        tag_query = f'nwr[{tags[category]}](around:{radius_m},{lat},{lon});'
    else:
        return ""
        
    return f"""[out:json][timeout:25];
(
  {tag_query}
);
out center;"""

async def fetch_poi(lat: float, lon: float, category: POICategory, radius_m: int) -> POIResult:
    """Fetch POI from Overpass API (OSM)."""
    if category == POICategory.OTHER:
        return POIResult(count=0, closest_distance_m=None)
        
    query = get_overpass_query(lat, lon, category, radius_m)
    if not query:
        return POIResult(count=0, closest_distance_m=None)
        
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(OVERPASS_URL, data=query)
        response.raise_for_status()
        data = response.json()
        
    elements = data.get("elements", [])
    if not elements:
        return POIResult(count=0, closest_distance_m=None)
        
    # Calculate closest distance using haversine
    from app.geo.distance import haversine
    closest = None
    for el in elements:
        # nodes have lat/lon directly, ways/relations have center if we used `out center`
        el_lat = el.get("lat") or el.get("center", {}).get("lat")
        el_lon = el.get("lon") or el.get("center", {}).get("lon")
        
        if el_lat and el_lon:
            dist = haversine(lat, lon, el_lat, el_lon)
            if closest is None or dist < closest:
                closest = dist
                
    return POIResult(count=len(elements), closest_distance_m=closest)
