import sys
sys.path.insert(0, 'src')
import requests
import pandas as pd
from geocoding import resolve_pickup_points

df = pd.read_excel('D:/RAWDATA/MAIN_SHEET_THREE_RAW_DATA.xlsx', sheet_name='E-Mail Data')
stops = [str(s).strip() for s in df['Pickup point'].dropna().unique() if str(s).strip()]
resolved = resolve_pickup_points(stops)

emps = []
for _, r in df.iterrows():
    p = str(r.get('Pickup point', '')).strip()
    if not p:
        continue
    info = resolved.get(p, {})
    emps.append({
        'employee_id': str(r.get('Employee ID No.', '')),
        'employee_name': str(r.get('Employee Name', '')),
        'shift': str(r.get('Shift', 'A')),
        'pickup_point': p,
        'latitude': info.get('latitude'),
        'longitude': info.get('longitude'),
        'location_status': info.get('location_status', 'LOCATION_UNVERIFIED'),
    })

res = requests.post('http://127.0.0.1:5000/api/optimize_all', json={'employees': emps, 'bus_capacity': 32})
print('Status code:', res.status_code)
data = res.json()
print('Total routes:', len(data.get('routes', [])))
for r in data.get('routes', []):
    corridor = r.get('corridor', '')
    stops_str = ' -> '.join([s['stop_name'] for s in r.get('stops', [])[:3]])
    print(f"{r['bus_id']} | {r['shift']} | {corridor:32} | Pax: {r['passengers_assigned']:2d} | Stops: {r['total_stops']:2d} | Km: {r['total_km']:5.1f} | Min: {r['total_minutes']:4.1f} | First stops: {stops_str}")
