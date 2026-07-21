import asyncio
import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

query = """[out:json][timeout:25];
(
  nwr["amenity"="kindergarten"](around:2000,55.75,37.61);
);
out center;"""

async def test():
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.post(OVERPASS_URL, data=query)
            response.raise_for_status()
            print("OK", len(response.json()["elements"]))
        except Exception as e:
            print("ERROR", repr(e))

asyncio.run(test())
