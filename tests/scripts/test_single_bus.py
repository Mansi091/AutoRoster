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
for s in stops:
    info = resolved.get(s, {})
    if info.get('latitude') and get_corridor(info['latitude'], info['longitude']) == 'South':
        south_stops.append({'stop_name': s, 'lat': info['latitude'], 'lon': info['longitude'], 'demand': 1})

uniq = {}
for s in south_stops:
    k = (round(s['lat'], 4), round(s['lon'], 4))
    if k not in uniq:
        uniq[k] = s
    else:
        uniq[k]['demand'] += 1

south_stops = list(uniq.values())
total_pax = sum(s['demand'] for s in south_stops)
print(f'South unique stops: {len(south_stops)}, total pax: {total_pax}')

unvisited = list(south_stops)
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
    if prev:
        tot_km += haversine_km(prev['lat'], prev['lon'], s['lat'], s['lon'])
    prev = s
final_leg = haversine_km(route[-1]['lat'], route[-1]['lon'], FACILITY_LAT, FACILITY_LON)
tot_km += final_leg
print(f'Single South Bus Distance: {tot_km:.1f} km, Est Time: {tot_km / 30 * 60:.1f} min')
for i, s in enumerate(route, 1):
    print(f"  {i}. {s['stop_name']} ({s['demand']} pax)")
