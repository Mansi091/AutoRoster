import urllib.request
import json

req = urllib.request.Request('http://127.0.0.1:5000/api/plan_multi_shift', data=b'{}', headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req, timeout=15) as res:
    d = json.loads(res.read())
    print('Status:', d['status'])
    print(f"Total Shift A routes: {len(d['shifts'][0]['routes'])}")
    for r in d['shifts'][0]['routes']:
        geom = r.get('road_geometry', [])
        print(f"Bus {r['route_no']}: {r['total_distance_km']} km, {r['route_duration_min']} min, {len(geom)} OSRM road coordinates, corridor: {r['corridor_name']}")
