import urllib.request
import urllib.parse
import json
from run_route_planner import load_all_verified_stops, DEFAULT_FACILITY

lookup = load_all_verified_stops()
nodes = [
    lookup['khedrajgurunagar'],
    lookup['ambethanchowkchakan'],
    lookup['kharabwadichakan'],
    DEFAULT_FACILITY
]

# 1. Test OSRM Table API
coords_str = ";".join([f"{n['lon']:.6f},{n['lat']:.6f}" for n in nodes])
table_url = f"https://router.project-osrm.org/table/v1/driving/{coords_str}?annotations=distance,duration"

req = urllib.request.Request(table_url, headers={"User-Agent": "AutoRoster/1.0"})
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print("TABLE API CODE:", data.get("code"))
    print("Durations (min):", [[round(s/60, 1) for s in row] for row in data["durations"]])
    print("Distances (km):", [[round(m/1000, 1) for m in row] for row in data["distances"]])

# 2. Test OSRM Route API for GeoJSON turn-by-turn road geometry
route_url = f"https://router.project-osrm.org/route/v1/driving/{coords_str}?overview=full&geometries=geojson"
req2 = urllib.request.Request(route_url, headers={"User-Agent": "AutoRoster/1.0"})
with urllib.request.urlopen(req2, timeout=10) as resp2:
    rdata = json.loads(resp2.read().decode("utf-8"))
    route = rdata["routes"][0]
    points = route["geometry"]["coordinates"]
    print("ROUTE API CODE:", rdata.get("code"))
    print("ROAD GEOMETRY POINTS COUNT:", len(points))
    print("FIRST 3 POINTS (lon, lat):", points[:3])
    print("ROAD DISTANCE KM:", round(route["distance"] / 1000, 1))
    print("ROAD DURATION MIN:", round(route["duration"] / 60, 1))
