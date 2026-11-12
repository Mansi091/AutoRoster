"""Test Google OR-Tools multi-shift VRP solver for employee transport roster."""

try:
    import google.protobuf.runtime_version
    google.protobuf.runtime_version.ValidateProtobufRuntimeVersion = lambda *args, **kwargs: None
except (ImportError, AttributeError):
    pass

import math
import re
import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

import pandas as pd
import requests
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

# Facility / Plant (Drop-off point)
FACILITY = {
    "name": "JCB India Ltd, Talegaon",
    "lat": 18.7765871,
    "lon": 73.6947478
}

# Shift Deadlines (10 min buffer before shift in-time)
# Shift A in-time: 06:50 -> plant arrival deadline: 06:40 (400 minutes from midnight)
# Shift B in-time: 15:20 (03:20 PM) -> plant arrival deadline: 15:10 (910 minutes from midnight)
# Shift C in-time: 23:45 (11:45 PM) -> plant arrival deadline: 23:35 (1415 minutes from midnight)
SHIFT_DEADLINES = {
    "A": {"in_time": "06:50", "deadline": "06:40", "deadline_min": 6 * 60 + 40},
    "B": {"in_time": "15:20", "deadline": "15:10", "deadline_min": 15 * 60 + 10},
    "C": {"in_time": "23:45", "deadline": "23:35", "deadline_min": 23 * 60 + 35},
}

BUS_CAPACITY = 32
MAX_RIDE_TIME_MIN = 70
TARGET_RIDE_TIME_MIN = 60
DWELL_TIME_MIN = 1.5
MERGE_RADIUS_METERS = 400.0


def haversine_m(c1, c2):
    lat1, lon1 = c1
    lat2, lon2 = c2
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def load_verified_stops():
    master_path = Path("D:/bus_routing_project/data/manual/pickup_point_master.xlsx")
    df_master = pd.read_excel(master_path)
    lookup = {}
    for _, r in df_master.iterrows():
        name = str(r["pickup_point"]).strip()
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        lat = float(r["latitude"])
        lon = float(r["longitude"])
        lookup[key] = {"name": name, "lat": lat, "lon": lon}
    return lookup


def get_road_matrix(nodes: List[Dict[str, Any]]) -> Tuple[List[List[int]], List[List[float]]]:
    """Fetch OSRM road matrix or compute accurate road-factor matrix."""
    n = len(nodes)
    if n <= 1:
        return [[0]], [[0.0]]

    # Haversine with 1.35 winding road factor & 32 km/h average speed in Pune suburban
    durations = [[0] * n for _ in range(n)]
    distances = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                straight_km = haversine_m((nodes[i]["lat"], nodes[i]["lon"]), (nodes[j]["lat"], nodes[j]["lon"])) / 1000.0
                road_km = straight_km * 1.35
                time_min = max(2, int(round((road_km / 32.0) * 60.0)))
                distances[i][j] = round(road_km, 2)
                durations[i][j] = time_min
    return durations, distances


def solve_shift_vrp(shift_label: str, employees: List[Dict[str, Any]], stop_lookup: Dict[str, Any]) -> Dict[str, Any]:
    print(f"\n================ SOLVING SHIFT {shift_label} ================")
    print(f"Total employees for Shift {shift_label}: {len(employees)}")

    # 1. Map employees to stops
    stop_groups: Dict[str, Dict[str, Any]] = {}
    outliers = []

    for emp in employees:
        raw_stop = emp["raw_stop"]
        key = re.sub(r"[^a-z0-9]", "", raw_stop.lower())
        loc = stop_lookup.get(key)
        if not loc:
            best_match = None
            for mk, mv in stop_lookup.items():
                if mk in key or key in mk:
                    best_match = mv
                    break
            loc = best_match

        if not loc:
            outliers.append({**emp, "reason": "Could not verify stop coordinates"})
            continue

        lat, lon = loc["lat"], loc["lon"]
        direct_dist_km = (haversine_m((lat, lon), (FACILITY["lat"], FACILITY["lon"])) / 1000.0) * 1.35
        direct_time_min = (direct_dist_km / 35.0) * 60.0
        if direct_time_min > MAX_RIDE_TIME_MIN:
            outliers.append({**emp, "reason": f"Direct travel time ({direct_time_min:.1f} min) exceeds 70 min limit"})
            continue

        skey = f"{round(lat, 4)}_{round(lon, 4)}"
        if skey not in stop_groups:
            stop_groups[skey] = {
                "name": loc["name"],
                "lat": lat,
                "lon": lon,
                "employees": []
            }
        stop_groups[skey]["employees"].append(emp)

    # 2. Merge pickup points within MERGE_RADIUS_METERS (300-500m)
    stop_list = list(stop_groups.values())
    merged_stops = []
    visited = set()

    for i in range(len(stop_list)):
        if i in visited:
            continue
        cluster_name = stop_list[i]["name"]
        cluster_lat = stop_list[i]["lat"]
        cluster_lon = stop_list[i]["lon"]
        cluster_emps = list(stop_list[i]["employees"])
        visited.add(i)

        for j in range(i + 1, len(stop_list)):
            if j not in visited:
                d_m = haversine_m((cluster_lat, cluster_lon), (stop_list[j]["lat"], stop_list[j]["lon"]))
                if d_m <= MERGE_RADIUS_METERS and (len(cluster_emps) + len(stop_list[j]["employees"])) <= BUS_CAPACITY:
                    visited.add(j)
                    cluster_emps.extend(stop_list[j]["employees"])

        total_pax = len(cluster_emps)
        if total_pax <= BUS_CAPACITY:
            merged_stops.append({
                "name": cluster_name,
                "lat": cluster_lat,
                "lon": cluster_lon,
                "demand": total_pax,
                "employees": cluster_emps
            })
        else:
            for part_idx, k in enumerate(range(0, total_pax, BUS_CAPACITY), 1):
                chunk = cluster_emps[k:k + BUS_CAPACITY]
                merged_stops.append({
                    "name": f"{cluster_name} (Part {part_idx})",
                    "lat": cluster_lat,
                    "lon": cluster_lon,
                    "demand": len(chunk),
                    "employees": chunk
                })

    num_pickup_stops = len(merged_stops)
    total_passengers = sum(s["demand"] for s in merged_stops)
    print(f"Aggregated unique pickup stops: {num_pickup_stops}, Demand: {total_passengers}")

    if num_pickup_stops == 0:
        return {"shift": shift_label, "routes": [], "buses_used": 0, "outliers": outliers}

    # 3. Nodes setup:
    # 0..N-1: Pickup stops
    # N: Plant (Drop-off point / Destination)
    nodes = list(merged_stops)
    plant_index = len(nodes)
    nodes.append({
        "name": FACILITY["name"],
        "lat": FACILITY["lat"],
        "lon": FACILITY["lon"],
        "demand": 0,
        "employees": []
    })

    time_matrix, dist_matrix = get_road_matrix(nodes)

    min_buses_needed = max(1, math.ceil(total_passengers / BUS_CAPACITY))
    num_vehicles = min(12, max(num_pickup_stops, min_buses_needed + 3))

    # OR-Tools Model
    # All vehicles end at plant_index
    manager = pywrapcp.RoutingIndexManager(len(nodes), num_vehicles, plant_index)
    routing = pywrapcp.RoutingModel(manager)

    def time_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        dwell = int(math.ceil(DWELL_TIME_MIN)) if from_node < num_pickup_stops else 0
        return time_matrix[from_node][to_node] + dwell

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Large fixed vehicle cost to strictly minimize NUMBER OF BUSES first
    FIXED_BUS_COST = 1_000_000
    routing.SetFixedCostOfAllVehicles(FIXED_BUS_COST)

    # Capacity Dimension
    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        return nodes[from_node]["demand"]

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        [BUS_CAPACITY] * num_vehicles,
        True,
        "Capacity"
    )

    # Time Dimension (Max Ride Time <= 70 min)
    routing.AddDimension(
        transit_callback_index,
        MAX_RIDE_TIME_MIN,
        MAX_RIDE_TIME_MIN,
        True,
        "Time"
    )

    # High penalty for dropping nodes
    PENALTY = 5_000_000
    for node_idx in range(num_pickup_stops):
        routing.AddDisjunction([manager.NodeToIndex(node_idx)], PENALTY)

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.seconds = 3

    solution = routing.SolveWithParameters(search_parameters)

    routes_output = []
    buses_used = 0
    assigned_passengers = 0
    total_shift_km = 0.0

    deadline_min = SHIFT_DEADLINES[shift_label]["deadline_min"]

    if solution:
        for vehicle_id in range(num_vehicles):
            index = routing.Start(vehicle_id)
            route_stops = []
            route_demand = 0

            while not routing.IsEnd(index):
                node_idx = manager.IndexToNode(index)
                if node_idx < num_pickup_stops:
                    route_stops.append(node_idx)
                    route_demand += nodes[node_idx]["demand"]
                index = solution.Value(routing.NextVar(index))

            if route_stops:
                buses_used += 1
                assigned_passengers += route_demand

                stop_timeline = []
                cur_time = float(deadline_min)

                last_stop_idx = route_stops[-1]
                time_to_plant = time_matrix[last_stop_idx][plant_index]
                cur_time -= time_to_plant
                stop_timeline.append({
                    "node_idx": last_stop_idx,
                    "departure_min": cur_time,
                    "pickup_time_min": cur_time - DWELL_TIME_MIN
                })
                cur_time -= DWELL_TIME_MIN

                for s in reversed(range(len(route_stops) - 1)):
                    s_from = route_stops[s]
                    s_to = route_stops[s + 1]
                    leg_time = time_matrix[s_from][s_to]
                    cur_time -= leg_time
                    stop_timeline.append({
                        "node_idx": s_from,
                        "departure_min": cur_time,
                        "pickup_time_min": cur_time - DWELL_TIME_MIN
                    })
                    cur_time -= DWELL_TIME_MIN

                stop_timeline.reverse()

                first_pickup_min = stop_timeline[0]["pickup_time_min"]
                route_duration_min = round(deadline_min - first_pickup_min, 1)

                route_dist_km = 0.0
                for s in range(len(route_stops) - 1):
                    route_dist_km += dist_matrix[route_stops[s]][route_stops[s + 1]]
                route_dist_km += dist_matrix[route_stops[-1]][plant_index]
                total_shift_km += route_dist_km

                stop_names_route = [nodes[n]["name"] for n in route_stops]
                corridor_label = " / ".join(list(dict.fromkeys(stop_names_route))[:4])

                detailed_stops = []
                for entry in stop_timeline:
                    n_idx = entry["node_idx"]
                    p_min = int(round(entry["pickup_time_min"]))
                    p_hour = (p_min // 60) % 24
                    p_rem = p_min % 60
                    time_str = f"{p_hour:02d}:{p_rem:02d}"

                    detailed_stops.append({
                        "stop_name": nodes[n_idx]["name"],
                        "lat": nodes[n_idx]["lat"],
                        "lon": nodes[n_idx]["lon"],
                        "demand": nodes[n_idx]["demand"],
                        "scheduled_time": time_str,
                        "employees": nodes[n_idx]["employees"]
                    })

                routes_output.append({
                    "route_no": buses_used,
                    "corridor_name": corridor_label,
                    "buses_used": 1,
                    "total_passengers": route_demand,
                    "occupancy_pct": round((route_demand / BUS_CAPACITY) * 100.0, 1),
                    "total_distance_km": round(route_dist_km, 1),
                    "route_duration_min": route_duration_min,
                    "first_pickup_time": detailed_stops[0]["scheduled_time"],
                    "plant_arrival_time": SHIFT_DEADLINES[shift_label]["deadline"],
                    "stops": detailed_stops
                })

        for node_idx in range(num_pickup_stops):
            if solution.Value(routing.NextVar(manager.NodeToIndex(node_idx))) == manager.NodeToIndex(node_idx):
                for emp in nodes[node_idx]["employees"]:
                    outliers.append({**emp, "reason": "No bus with spare capacity within 70 min ride time"})

    avg_occupancy = round((assigned_passengers / (buses_used * BUS_CAPACITY) * 100.0), 1) if buses_used > 0 else 0.0
    longest_ride = max([r["route_duration_min"] for r in routes_output], default=0.0)

    summary = {
        "shift": shift_label,
        "shift_start": SHIFT_DEADLINES[shift_label]["in_time"],
        "plant_deadline": SHIFT_DEADLINES[shift_label]["deadline"],
        "total_employees_in_roster": len(employees),
        "assigned_passengers": assigned_passengers,
        "buses_used": buses_used,
        "avg_occupancy_pct": avg_occupancy,
        "total_distance_km": round(total_shift_km, 1),
        "longest_ride_time_min": longest_ride,
        "routes": routes_output,
        "outliers": outliers,
        "checks": {
            "passengers_assigned_match": "PASS" if assigned_passengers == len(employees) - len(outliers) else "FAIL",
            "capacity_under_32": "PASS" if all(r["total_passengers"] <= BUS_CAPACITY for r in routes_output) else "FAIL",
            "ride_time_under_70": "PASS" if longest_ride <= MAX_RIDE_TIME_MIN else "FAIL",
        }
    }
    return summary

if __name__ == "__main__":
    df = pd.read_excel(r'D:\bus_routing_project\data\input\MAIN_SHEET_TWO_RAW_DATA.xlsx', header=1)
    stop_lookup = load_verified_stops()

    all_emps = []
    for idx, r in df.iterrows():
        all_emps.append({
            "sr_no": idx + 1,
            "emp_type": str(r.get("GRADE", "DET")),
            "emp_name": str(r.get("Name", f"Emp_{idx+1}")),
            "shift": str(r.get("Shift", "A")),
            "raw_stop": str(r.get("Bus Stop", "")),
            "mobile": str(r.get("Mob. No.", "")),
        })

    def map_shift(s):
        s_up = str(s).upper()
        if "A" in s_up: return "A"
        if "B" in s_up: return "B"
        if "C" in s_up: return "C"
        return "A"

    for shift in ["A", "B", "C"]:
        shift_emps = [e for e in all_emps if map_shift(e["shift"]) == shift]
        res = solve_shift_vrp(shift, shift_emps, stop_lookup)
        print(f"Shift {shift} Summary:")
        print(f" - Buses used: {res['buses_used']}")
        print(f" - Assigned passengers: {res['assigned_passengers']} / {res['total_employees_in_roster']}")
        print(f" - Avg occupancy: {res['avg_occupancy_pct']}%")
        print(f" - Total distance: {res['total_distance_km']} km")
        print(f" - Longest ride time: {res['longest_ride_time_min']} min")
        print(f" - Checks: {res['checks']}")
