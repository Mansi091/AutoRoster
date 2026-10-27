import sys
sys.path.insert(0, 'src')
import pandas as pd
from aggregator_cvrptw import haversine_km, fetch_road_time_matrix, DEFAULT_FACILITY
from optimizer_cvrptw import optimize_routes
from geocoding import resolve_pickup_points

FACILITY_LAT, FACILITY_LON = 18.7765871, 73.6947478

def get_corridor(lat, lon):
    d = haversine_km(lat, lon, FACILITY_LAT, FACILITY_LON)
    if d <= 5.5: return 'Talegaon Local'
    if lon >= 73.80: return 'East (Chakan/Moshi/Bhosari)'
    if lon <= 73.66: return 'West (Vadgaon/Lonavala)'
    if lat < 18.73: return 'South (PCMC/Nigdi/Chinchwad)'
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
    if info.get('latitude') and get_corridor(info['latitude'], info['longitude']) == 'South (PCMC/Nigdi/Chinchwad)':
        south_stops.append({'stop_name': p, 'lat': info['latitude'], 'lon': info['longitude'], 'demand': 1, 'service_minutes': 1})

# Aggregate by stop
grp = {}
for s in south_stops:
    k = (round(s['lat'], 4), round(s['lon'], 4))
    if k not in grp:
        grp[k] = s
    else:
        grp[k]['demand'] += 1

c_stops = list(grp.values())
total_pax = sum(s['demand'] for s in c_stops)
print('Total South stops:', len(c_stops), 'Total South pax:', total_pax)

depot_lat = sum(s['lat'] for s in c_stops) / len(c_stops)
depot_lon = sum(s['lon'] for s in c_stops) / len(c_stops)
depot_node = {'stop_name': 'Depot', 'lat': depot_lat, 'lon': depot_lon, 'demand': 0, 'service_minutes': 0}
dest_node = {'stop_name': 'JCB India Ltd', 'lat': FACILITY_LAT, 'lon': FACILITY_LON, 'demand': 0, 'service_minutes': 0}
all_nodes = [depot_node, *c_stops, dest_node]

time_matrix, dist_matrix = fetch_road_time_matrix(all_nodes)

# Try solving with EXACTLY 1 bus
res = optimize_routes(
    time_matrix=time_matrix,
    demands=[n['demand'] for n in all_nodes],
    stop_names=[n['stop_name'] for n in all_nodes],
    service_minutes=[n['service_minutes'] for n in all_nodes],
    num_buses=1,
    earliest_departure=330,
    latest_departure=380,
    shift_start=410,
    max_route_minutes=70,
    arrival_buffer=10,
    bus_capacity=32,
    time_limit_seconds=3
)
print('OR-Tools Status with 1 bus:', res.get('status'), 'routes:', len(res.get('routes', [])))
for r in res.get('routes', []):
    dur = r.get('route_duration_minutes', 0)
    pax = r.get('total_passengers', 0)
    st = [s['stop_name'] for s in r['stops'] if s['node_index'] not in (0, len(all_nodes)-1)]
    print(f'Route duration: {dur} min, pax: {pax}')
    print(f'Stops ({len(st)}): {" -> ".join(st)}')
