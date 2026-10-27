import sys
sys.path.insert(0, 'src')
import pandas as pd
from aggregator_cvrptw import haversine_km
from geocoding import resolve_pickup_points

FACILITY_LAT, FACILITY_LON = 18.7765871, 73.6947478

def get_corridor(lat, lon):
    d = haversine_km(lat, lon, FACILITY_LAT, FACILITY_LON)
    if d <= 5.5: return 'Talegaon Local'
    if lon >= 73.80: return 'East'

    if lon <= 73.66: return 'West'
    if lat < 18.73: return 'South'
    return 'Talegaon Local'

df = pd.read_excel('data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx', header=1)
stops = [str(s).strip() for s in df['Bus Stop'].dropna().unique() if str(s).strip()]
resolved = resolve_pickup_points(stops)

south_stops = []
for _, r in df.iterrows():
    p = str(r.get('Bus Stop', '')).strip()
    shift = str(r.get('Shift', 'A'))
    if not p or 'A' not in shift: continue
    info = resolved.get(p, {})
    if info.get('latitude') and get_corridor(info['latitude'], info['longitude']) == 'South':
        south_stops.append({'name': p, 'lat': info['latitude'], 'lon': info['longitude']})

grp = pd.DataFrame(south_stops).groupby(['name', 'lat', 'lon']).size().reset_index(name='count')
st_list = grp.to_dict('records')
total_pax = sum(s['count'] for s in st_list)
print(f'South Shift A stops count: {len(st_list)}, total passengers: {total_pax}')

unvisited = list(st_list)
start = max(unvisited, key=lambda s: haversine_km(s['lat'], s['lon'], FACILITY_LAT, FACILITY_LON))
route = [start]
unvisited.remove(start)
while unvisited:
    nxt = min(unvisited, key=lambda s: haversine_km(route[-1]['lat'], route[-1]['lon'], s['lat'], s['lon']))
    route.append(nxt)
    unvisited.remove(nxt)

tot_km = 0
prev = None
for s in route:
    if prev: tot_km += haversine_km(prev['lat'], prev['lon'], s['lat'], s['lon'])
    prev = s
final_leg = haversine_km(route[-1]['lat'], route[-1]['lon'], FACILITY_LAT, FACILITY_LON)
tot_km += final_leg
print(f'Single South Bus Total Distance: {tot_km:.1f} km, Est Travel Time: {tot_km/30*60:.1f} min')
for i, s in enumerate(route, 1):
    print(f"  {i}. {s['name']} ({s['count']} pax)")
