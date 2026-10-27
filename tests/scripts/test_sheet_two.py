import sys
sys.path.insert(0, 'src')
import pandas as pd
from geocoding import resolve_pickup_points
import requests

df = pd.read_excel('data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx', header=1)
stop_col = 'Bus Stop' if 'Bus Stop' in df.columns else 'Pickup point'
stops = [str(s).strip() for s in df[stop_col].dropna().unique() if str(s).strip()]
resolved = resolve_pickup_points(stops)

emps = []
for _, r in df.iterrows():
    p = str(r.get(stop_col, '')).strip()
    if not p:
        continue
    info = resolved.get(p, {})
    emps.append({
        'employee_id': str(r.get('EMP ID', r.get('Employee ID No.', ''))),
        'employee_name': str(r.get('Name', r.get('Employee Name', ''))),
        'shift': str(r.get('Shift', 'A')),
        'pickup_point': p,
        'latitude': info.get('latitude'),
        'longitude': info.get('longitude'),
        'location_status': info.get('location_status', 'LOCATION_UNVERIFIED'),
    })

res = requests.post('http://127.0.0.1:5000/api/optimize_all', json={'employees': emps, 'bus_capacity': 32})
data = res.json()
print('Total routes:', len(data.get('routes', [])))
for r in data.get('routes', []):
    if 'A' in r['shift']:
        print(f"{r['bus_id']} | {r['corridor']} | Pax: {r['passengers_assigned']} | Stops: {r['total_stops']} | Km: {r['total_km']} | Min: {r['total_minutes']}")
        for s in r['stops']:
            print(f"   - {s['stop_name']} ({s['lat']}, {s['lon']}) -> {s['passengers_boarding']} pax, {s['distance_to_destination_km']}km")
