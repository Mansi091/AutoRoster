from run_route_planner import load_all_verified_stops, DEFAULT_FACILITY, haversine_distance_m, BUS_CAPACITY, DWELL_TIME_MIN, MAX_RIDE_TIME_MIN, SHIFT_CONFIG
import pandas as pd
import math
import re
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

df = pd.read_excel('D:/bus_routing_project/data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx', header=1)
emps = [
    {
        'sr_no': i + 1,
        'emp_name': str(r.get('Name', '')),
        'emp_type': str(r.get('GRADE', 'DET')),
        'shift': str(r.get('Shift', 'A')).strip(),
        'raw_stop': str(r.get('Bus Stop', '')).strip(),
        'mobile': str(r.get('Mob. No.', ''))
    }
    for i, r in df.iterrows() if 'A' in str(r.get('Shift', '')).upper()
]

lookup = load_all_verified_stops()
facility = DEFAULT_FACILITY

stop_groups = {}
for emp in emps:
    raw = emp['raw_stop']
    k = re.sub(r'[^a-z0-9]', '', raw.lower())
    loc = lookup.get(k)
    if not loc:
        for mk, mv in lookup.items():
            if mk in k or k in mk:
                loc = mv
                break
    if loc:
        ck = f"{round(loc['lat'], 4)}_{round(loc['lon'], 4)}"
        if ck not in stop_groups:
            stop_groups[ck] = {'name': loc['name'], 'lat': loc['lat'], 'lon': loc['lon'], 'employees': []}
        stop_groups[ck]['employees'].append(emp)

stop_list = list(stop_groups.values())
merged_stops = [{'name': s['name'], 'lat': s['lat'], 'lon': s['lon'], 'demand': len(s['employees']), 'employees': s['employees']} for s in stop_list]

num_pickups = len(merged_stops)
nodes = list(merged_stops)
plant_node = num_pickups
start_node = num_pickups + 1

nodes.append({'name': facility['name'], 'lat': facility['lat'], 'lon': facility['lon'], 'demand': 0})
nodes.append({'name': 'Virtual Start', 'lat': facility['lat'], 'lon': facility['lon'], 'demand': 0})

num_nodes = len(nodes)

# Travel matrix with 38 km/h speed
durations = [[0] * num_nodes for _ in range(num_nodes)]
distances = [[0.0] * num_nodes for _ in range(num_nodes)]
for i in range(plant_node + 1):
    for j in range(plant_node + 1):
        if i != j:
            straight_km = haversine_distance_m((nodes[i]["lat"], nodes[i]["lon"]), (nodes[j]["lat"], nodes[j]["lon"])) / 1000.0
            road_km = straight_km * 1.28
            durations[i][j] = max(2, int(round((road_km / 38.0) * 60.0)))
            distances[i][j] = round(road_km, 2)

num_vehicles = 10
starts = [start_node] * num_vehicles
ends = [plant_node] * num_vehicles

manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles, starts, ends)
routing = pywrapcp.RoutingModel(manager)

def time_callback(from_index, to_index):
    u = manager.IndexToNode(from_index)
    v = manager.IndexToNode(to_index)
    if u == start_node: return 0
    dwell = 1 if u < num_pickups else 0
    return durations[u][v] + dwell

cb_idx = routing.RegisterTransitCallback(time_callback)
routing.SetArcCostEvaluatorOfAllVehicles(cb_idx)
routing.SetFixedCostOfAllVehicles(1_000_000)

demand_cb = routing.RegisterUnaryTransitCallback(lambda from_idx: nodes[manager.IndexToNode(from_idx)]['demand'])
routing.AddDimensionWithVehicleCapacity(demand_cb, 0, [32] * num_vehicles, True, "Capacity")
routing.AddDimension(cb_idx, 70, 70, True, "Time")

for i in range(num_pickups):
    routing.AddDisjunction([manager.NodeToIndex(i)], 5_000_000)

p = pywrapcp.DefaultRoutingSearchParameters()
p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
p.time_limit.seconds = 3

sol = routing.SolveWithParameters(p)

print("SOLVED! Routes:")
for v in range(num_vehicles):
    idx = routing.Start(v)
    route_nodes = []
    while not routing.IsEnd(idx):
        u = manager.IndexToNode(idx)
        if u != start_node:
            route_nodes.append(nodes[u]['name'])
        idx = sol.Value(routing.NextVar(idx))
    if route_nodes:
        print(f"Vehicle {v+1}: {route_nodes}")
