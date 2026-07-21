import json
from app.geo.distance import is_center, haversine, MOSCOW_CENTER_LAT, MOSCOW_CENTER_LON
from app.reference.loader import load_all

def main():
    ref_data = load_all()
    complexes_with_dist = []
    
    for c in ref_data.complexes:
        if c.lat is not None and c.lon is not None:
            dist = haversine(MOSCOW_CENTER_LAT, MOSCOW_CENTER_LON, c.lat, c.lon)
            complexes_with_dist.append((c.name, dist, c.lat, c.lon))
    
    complexes_with_dist.sort(key=lambda x: x[1])
    
    print(f"Top 10 closest complexes to the center:")
    for name, dist, lat, lon in complexes_with_dist[:10]:
        print(f"- {name} (distance: {dist/1000:.2f} km)")

if __name__ == "__main__":
    main()
