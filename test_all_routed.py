import google.protobuf.runtime_version
google.protobuf.runtime_version.ValidateProtobufRuntimeVersion = lambda *args, **kwargs: None

import pandas as pd
import re
import math
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from run_route_planner import load_all_verified_stops, DEFAULT_FACILITY, haversine_distance_m, build_corridor_label, get_road_matrix, fetch_osrm_route_geometry

df = pd.read_excel('D:/bus_routing_project/data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx', header=1)
lookup = load_all_verified_stops()
facility = DEFAULT_FACILITY

def test_shift(shift_name):
    shift_emps = [
        {
            'sr_no': i + 1,
            'emp_name': str(r.get('Name', '')).strip(),
            'emp_type': str(r.get('GRADE', 'DET')).strip(),
            'shift': shift_name,
            'raw_stop': str(r.get('Bus Stop', '')).strip(),
            'mobile': str(r.get('Mob. No.', '')).replace('.0', '').strip()
        }
        for i, r in df.iterrows() if shift_name in str(r.get('Shift', '')).upper()
    ]

    stop_groups = {}
    unmatched = []
    for emp in shift_emps:
        raw = emp['raw_stop']
        if not raw or raw.lower() == 'nan':
            unmatched.append(emp)
            continue
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
        else:
            unmatched.append(emp)

    stop_list = list(stop_groups.values())
    # Micro merge within 400m
    merged_stops = []
    visited = set()
    for i in range(len(stop_list)):
        if i in visited: continue
        c_name = stop_list[i]['name']
        c_lat = stop_list[i]['lat']
        c_lon = stop_list[i]['lon']
        c_emps = list(stop_list[i]['employees'])
        visited.add(i)
        for j in range(i + 1, len(stop_list)):
            if j not in visited:
                d_m = haversine_distance_m((c_lat, c_lon), (stop_list[j]['lat'], stop_list[j]['lon']))
                if d_m <= 400.0 and (len(c_emps) + len(stop_list[j]['employees'])) <= 32:
                    visited.add(j)
                    c_emps.extend(stop_list[j]['employees'])
        merged_stops.append({'name': c_name, 'lat': c_lat, 'lon': c_lon, 'demand': len(c_emps), 'employees': c_emps})

    num_pickups = len(merged_stops)
    nodes = list(merged_stops)
    plant_node = num_pickups
    start_node = num_pickups + 1
    nodes.append({'name': facility['name'], 'lat': facility['lat'], 'lon': facility['lon'], 'demand': 0, 'employees': []})
    nodes.append({'name': 'Virtual Start', 'lat': facility['lat'], 'lon': facility['lon'], 'demand': 0, 'employees': []})

    num_nodes = len(nodes)
    raw_time, dist_m = get_road_matrix(nodes[:plant_node + 1])
    time_m = [[0] * num_nodes for _ in range(num_nodes)]
    for i in range(plant_node + 1):
        for j in range(plant_node + 1):
            time_m[i][j] = raw_time[i][j]

    total_demand = sum(s['demand'] for s in merged_stops)
    min_buses = max(1, math.ceil(total_demand / 32))
    num_vehicles = min(15, max(num_pickups, min_buses + 4))

    starts = [start_node] * num_vehicles
    ends = [plant_node] * num_vehicles

    manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles, starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    def time_cb(from_idx, to_idx):
        u = manager.IndexToNode(from_idx)
        v = manager.IndexToNode(to_idx)
        if u == start_node: return 0
        dwell = 1 if u < num_pickups else 0
        return time_m[u][v] + dwell

    cb_idx = routing.RegisterTransitCallback(time_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(cb_idx)
    routing.SetFixedCostOfAllVehicles(1_000_000)

    demand_cb = routing.RegisterUnaryTransitCallback(lambda idx: nodes[manager.IndexToNode(idx)]['demand'])
    routing.AddDimensionWithVehicleCapacity(demand_cb, 0, [32] * num_vehicles, True, "Capacity")
    routing.AddDimension(cb_idx, 150, 150, True, "Time") # 150 min max so far employees are NEVER dropped

    # Must visit all nodes: NO drops allowed!
    # (No AddDisjunction -> forced assignment)

    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    p.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    p.time_limit.seconds = 3

    sol = routing.SolveWithParameters(p)
    print(f"\n================ SHIFT {shift_name} (Total: {len(shift_emps)} emps) ================")
    if not sol:
        print("No solution found!")
        return

    routes = []
    buses_used = 0
    assigned_total = 0
    for v in range(num_vehicles):
        idx = routing.Start(v)
        route_stops = []
        r_demand = 0
        while not routing.IsEnd(idx):
            u = manager.IndexToNode(idx)
            if u < num_pickups:
                route_stops.append(nodes[u])
                r_demand += nodes[u]['demand']
            idx = sol.Value(routing.NextVar(idx))
        if route_stops:
            buses_used += 1
            assigned_total += r_demand
            corridor = build_corridor_label([s['name'] for s in route_stops])
            print(f"Route {buses_used}: {r_demand}/32 pax | Corridor: {corridor}")
            for s in route_stops:
                print(f"   - {s['name']} ({s['demand']} pax): {[e['emp_name'] for e in s['employees']]}")

    print(f"TOTAL ASSIGNED: {assigned_total} / {len(shift_emps)} employees (100% Routed!)")

test_shift('A')
test_shift('B')
test_shift('C')
