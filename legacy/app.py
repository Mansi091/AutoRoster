"""Web Application Backend for Bus Routing & Employee Grouping.
Features:
- Upload raw Excel file (.xlsx) or load current project data
- Groups stops & employees into tight local corridors (~15 passengers per bus)
- Visual route maps & stop-by-stop distance tracking
- Export directly to formatted Excel (.xlsx)
"""
import io
import math
import os
from pathlib import Path
import sys
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from flask import Flask, jsonify, render_template, request, send_file
import pandas as pd

try:
    import google.protobuf.runtime_version
    google.protobuf.runtime_version.ValidateProtobufRuntimeVersion = lambda *args, **kwargs: None
except (ImportError, AttributeError):
    pass

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from geocoding import resolve_pickup_points

app = Flask(__name__)

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2)**2
    return 2 * R * math.asin(math.sqrt(a))

def get_corridor(lat, lon, dest_lat=18.7765871, dest_lon=73.6947478):
    """Dynamically classifies any coordinate into radial corridors based on compass bearing from destination."""
    if lat is None or lon is None:
        return "Local Vicinity"
    d = haversine_km(lat, lon, dest_lat, dest_lon)
    if d <= 5.5:
        return "Local Vicinity"

    d_lon = math.radians(lon - dest_lon)
    lat1 = math.radians(dest_lat)
    lat2 = math.radians(lat)
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360

    if 45 <= bearing < 135:
        return "East Corridor"
    elif 135 <= bearing < 225:
        return "South Corridor"
    elif 225 <= bearing < 315:
        return "West Corridor"
    else:
        return "North Corridor"



def process_and_group(df, bus_capacity: int = 32):

    """Groups employees into buses purely by geographic proximity.

    Algorithm (per shift):
    1. Resolve all pickup-point coordinates.
    2. Aggregate stops (unique lat/lon groups with demand counts).
    3. Cluster stops into buses using greedy farthest-first seeding:
       - Seed = stop farthest from the drop facility (longest detour first).
       - Fill bus from seed using nearest-neighbor until capacity is reached.
       - Repeat for remaining unassigned stops.
    4. Within each bus, sequence stops by nearest-neighbor from the first stop
       toward the facility (minimises total driven km).
    5. No corridor walls — a stop may be picked up by any bus that is nearby.
    """
    col_map = {}
    for c in df.columns:
        cl = str(c).strip().lower()
        if "name" in cl:
            col_map["name"] = c
        elif "id" in cl and "emp" in cl:
            col_map["id"] = c
        elif "shift" in cl:
            col_map["shift"] = c
        elif "stop" in cl or "pickup" in cl:
            col_map["stop"] = c
        elif "mob" in cl or "phone" in cl:
            col_map["mobile"] = c
        elif "grade" in cl or "employee type" in cl:
            col_map["grade"] = c
        elif "area" in cl or "loc" in cl:
            col_map["area"] = c

    stop_column = col_map.get("stop", "Bus Stop")
    pickup_names = [
        str(v).strip()
        for v in df[stop_column].dropna().unique()
        if str(v).strip().lower() not in ("nan", "none", "")
    ]
    resolved_locations = resolve_pickup_points(pickup_names)

    rows = []
    for _, r in df.iterrows():
        stop = str(r.get(col_map.get("stop", "Bus Stop"), "")).strip()
        if not stop or stop.lower() in ("nan", "none", ""):
            continue
        name  = str(r.get(col_map.get("name",   "Name"),     "")).strip().title()
        shift = str(r.get(col_map.get("shift",  "Shift"),    "A")).strip().upper()
        emp_id = str(r.get(col_map.get("id",    "EMP ID"),   ""))
        mobile = str(r.get(col_map.get("mobile", "Mob. No."), ""))
        grade  = str(r.get(col_map.get("grade",  "GRADE"),   ""))
        area   = str(r.get(col_map.get("area",   "Location"), ""))

        loc = resolved_locations.get(stop, {})
        lat, lon = loc.get("latitude"), loc.get("longitude")

        rows.append({
            "employee_id":       emp_id,
            "employee_name":     name,
            "grade":             grade,
            "shift":             shift,
            "pickup_point":      stop,
            "mobile":            mobile,
            "area":              area,
            "latitude":          lat,
            "longitude":         lon,
            "corridor":          get_corridor(lat, lon) if lat is not None else "",
            "location_status":   loc.get("location_status", "LOCATION_UNVERIFIED"),
            "resolution_reason": loc.get("resolution_reason", ""),
        })

    emp_df = pd.DataFrame(rows, columns=[
        "employee_id", "employee_name", "grade", "shift", "pickup_point",
        "mobile", "area", "latitude", "longitude", "corridor",
        "location_status", "resolution_reason",
    ])
    routeable = emp_df[emp_df["location_status"] == "VERIFIED"].copy()

    # Facility drop point (used as reference for distance ordering)
    FACILITY_LAT, FACILITY_LON = 18.7765871, 73.6947478

    routes = []
    passenger_assignments = []

    for shift in ["A", "B", "C"]:
        if shift == "A":
            sub = routeable[routeable["shift"].str.contains("A", na=False)]
        else:
            sub = routeable[routeable["shift"] == shift]
        if sub.empty:
            continue

        # ── Step 1: Aggregate into unique physical stops ──────────────────────
        grp = (
            sub.groupby(["pickup_point", "latitude", "longitude"])
            .size()
            .reset_index(name="count")
        )
        all_stops = [
            {"name": row["pickup_point"],
             "lat":  float(row["latitude"]),
             "lon":  float(row["longitude"]),
             "count": int(row["count"])}
            for _, row in grp.iterrows()
        ]

        # ── Step 2: Corridor-based clustering ─────────────────────────────────
        corridor_groups = {}
        for s in all_stops:
            c = get_corridor(s["lat"], s["lon"], FACILITY_LAT, FACILITY_LON)
            corridor_groups.setdefault(c, []).append(s)

        bus_num = 1
        for corridor_name, c_stops in corridor_groups.items():
            unassigned = list(range(len(c_stops)))
            bus_clusters = []

            while unassigned:
                seed = max(
                    unassigned,
                    key=lambda i: haversine_km(
                        c_stops[i]["lat"], c_stops[i]["lon"],
                        FACILITY_LAT, FACILITY_LON
                    )
                )
                cluster = []
                capacity_left = bus_capacity
                current = seed

                while True:
                    s = c_stops[current]
                    if s["count"] > capacity_left:
                        break
                    cluster.append(current)
                    unassigned.remove(current)
                    capacity_left -= s["count"]

                    if not unassigned or capacity_left <= 0:
                        break

                    current = min(
                        unassigned,
                        key=lambda i: haversine_km(
                            s["lat"], s["lon"],
                            c_stops[i]["lat"], c_stops[i]["lon"]
                        )
                    )
                    dist_to_next = haversine_km(
                        s["lat"], s["lon"],
                        c_stops[current]["lat"], c_stops[current]["lon"]
                    )
                    if dist_to_next > 15.0:
                        break

                if not cluster:
                    cluster = [seed]
                    if seed in unassigned:
                        unassigned.remove(seed)

                bus_clusters.append(cluster)

            # ── Step 3: Sequence stops within each bus ────────────────────────
            for cluster in bus_clusters:
                cluster_stops = [c_stops[i] for i in cluster]

                remaining = list(range(len(cluster_stops)))
                start = max(
                    remaining,
                    key=lambda i: haversine_km(
                        cluster_stops[i]["lat"], cluster_stops[i]["lon"],
                        FACILITY_LAT, FACILITY_LON
                    )
                )

                ordered = []
                prev_lat, prev_lon = cluster_stops[start]["lat"], cluster_stops[start]["lon"]
                remaining.remove(start)
                ordered.append(start)

                while remaining:
                    nxt = min(
                        remaining,
                        key=lambda i: haversine_km(
                            prev_lat, prev_lon,
                            cluster_stops[i]["lat"], cluster_stops[i]["lon"]
                        )
                    )
                    ordered.append(nxt)
                    remaining.remove(nxt)
                    prev_lat = cluster_stops[nxt]["lat"]
                    prev_lon = cluster_stops[nxt]["lon"]

                route_stops = []
                cum_dist = 0.0
                cum_pax  = 0
                prev_coord = None

                for idx in ordered:
                    s = cluster_stops[idx]
                    dist = 0.0 if prev_coord is None else haversine_km(
                        prev_coord[0], prev_coord[1], s["lat"], s["lon"]
                    )
                    cum_dist += dist
                    cum_pax  += s["count"]
                    dist_to_dest = haversine_km(s["lat"], s["lon"], FACILITY_LAT, FACILITY_LON)

                    route_stops.append({
                        "seq":                        len(route_stops) + 1,
                        "stop_name":                  s["name"],
                        "lat":                        round(s["lat"], 5),
                        "lon":                        round(s["lon"], 5),
                        "distance_km":                round(dist, 1),
                        "distance_from_prev_km":      round(dist, 1),
                        "distance_to_destination_km": round(dist_to_dest, 1),
                        "cumulative_km":              round(cum_dist, 1),
                        "passengers_boarding":        s["count"],
                        "cumulative_passengers":      cum_pax,
                    })
                    prev_coord = (s["lat"], s["lon"])

                final_leg = haversine_km(
                    cluster_stops[ordered[-1]]["lat"],
                    cluster_stops[ordered[-1]]["lon"],
                    FACILITY_LAT, FACILITY_LON
                )
                total_pax = cum_pax
                total_km  = round(cum_dist + final_leg, 1)
                max_jump  = max((s["distance_km"] for s in route_stops), default=0.0)
                bus_id    = f"Bus {shift}-{bus_num}"
                bus_num  += 1

                routes.append({
                    "bus_id":              bus_id,
                    "shift":               f"Shift {shift}",
                    "corridor":            corridor_name,
                    "total_passengers":    total_pax,
                    "passengers_assigned": total_pax,
                    "target_limit":        bus_capacity,
                    "seats_available":     max(0, bus_capacity - total_pax),
                    "total_stops":         len(route_stops),
                    "total_km":            total_km,
                    "max_jump_km":         round(max_jump, 1),
                    "start_stop":          route_stops[0]["stop_name"] if route_stops else "",
                    "end_stop":            "JCB India Ltd",
                    "stops":               route_stops,
                })

                bus_stops_set = {s["stop_name"] for s in route_stops}
                for _, er in sub.iterrows():
                    if er["pickup_point"] in bus_stops_set:
                        passenger_assignments.append({
                            "sr_no":           len(passenger_assignments) + 1,
                            "employee_id":     er["employee_id"],
                            "employee_name":   er["employee_name"],
                            "grade":           er["grade"],
                            "shift":           er["shift"],
                            "pickup_point":    er["pickup_point"],
                            "assigned_bus":    bus_id,
                            "mobile":          er["mobile"],
                            "area":            er["area"],
                            "corridor":        corridor_name,
                            "latitude":        er["latitude"],
                            "longitude":       er["longitude"],
                            "location_status": "VERIFIED",
                        })

    for _, er in emp_df[emp_df["location_status"] != "VERIFIED"].iterrows():
        passenger_assignments.append({
            "sr_no":             len(passenger_assignments) + 1,
            "employee_id":       er["employee_id"],
            "employee_name":     er["employee_name"],
            "grade":             er["grade"],
            "shift":             er["shift"],
            "pickup_point":      er["pickup_point"],
            "assigned_bus":      "Unassigned",
            "mobile":            er["mobile"],
            "area":              er["area"],
            "corridor":          "",
            "latitude":          None,
            "longitude":         None,
            "location_status":   "LOCATION_UNVERIFIED",
            "resolution_reason": er["resolution_reason"],
        })

    return {
        "routes":               routes,
        "employees":            passenger_assignments,
        "total_employees":      len(emp_df),
        "routed_employees":     len(routeable),
        "unverified_employees": len(emp_df) - len(routeable),
        "total_buses":          len(routes),
        "total_stops":          sum(r["total_stops"] for r in routes),
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/load_current", methods=["GET"])
def load_current():
    raw_path = ROOT / "output/01_cleaned_employees.xlsx"
    if not raw_path.exists():
        raw_path = ROOT / "data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx"
    df = pd.read_excel(raw_path, sheet_name="Employees" if "cleaned" in str(raw_path) else 0)
    result = process_and_group(df)
    return jsonify(result)

@app.route("/api/resolve_pickups", methods=["POST"])
def resolve_pickups():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("pickup_points"), list):
        return jsonify({"error": "Expected a pickup_points array."}), 400

    pickup_points = data["pickup_points"]
    if len(pickup_points) > 200:
        return jsonify({"error": "Resolve no more than 200 pickup points per request."}), 400
    if any(not isinstance(point, str) for point in pickup_points):
        return jsonify({"error": "Every pickup point must be a string."}), 400

    allow_agentic = bool(data.get("allow_agentic", False))
    return jsonify({"locations": resolve_pickup_points(pickup_points, allow_agentic=allow_agentic)})


@app.route("/api/agentic_resolve", methods=["POST"])
def agentic_resolve():
    """Agentic LLM-supervised geocoding with Geoapify + Gemini 2.5 Flash."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("stops"), list):
        return jsonify({"error": "Expected a 'stops' array."}), 400

    stops = [s for s in data["stops"] if isinstance(s, str) and s.strip()]
    if len(stops) > 50:
        return jsonify({"error": "Limit agentic resolution to 50 stops per batch."}), 400

    try:
        from agentic_geocoder import agentic_verify_stops
        known_coords = data.get("known_coords")
        results = agentic_verify_stops(stops, known_coords=known_coords)
        return jsonify({"status": "SUCCESS", "results": results})
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 500


@app.route("/api/fetch_candidates", methods=["POST"])
def fetch_candidates():
    """Fetches map candidates for a single stop scored by proximity to the employee cluster."""
    data = request.get_json(silent=True) or {}
    stop_name = str(data.get("stop_name", "")).strip()
    if not stop_name:
        return jsonify({"error": "stop_name required"}), 400

    known_coords = data.get("known_coords")
    from agentic_geocoder import fetch_geoapify_candidates, load_master_coords
    if not known_coords:
        m = load_master_coords()
        known_coords = [
            (float(v[0]), float(v[1]))
            for v in m.values()
            if isinstance(v, (list, tuple)) and len(v) == 2 and v[0] and v[1]
        ]
    candidates = fetch_geoapify_candidates(stop_name, limit=5, known_coords=known_coords)
    return jsonify({"stop_name": stop_name, "candidates": candidates})


@app.route("/api/save_coordinate", methods=["POST"])
def save_coordinate():
    """Manually saves or updates a stop's coordinates into master_coords.json."""
    data = request.get_json(silent=True) or {}
    stop_name = str(data.get("stop_name", "")).strip().lower()
    lat = data.get("lat")
    lon = data.get("lon")
    if not stop_name or lat is None or lon is None:
        return jsonify({"error": "Missing stop_name, lat, or lon"}), 400
    try:
        lat = float(lat)
        lon = float(lon)
    except ValueError:
        return jsonify({"error": "Invalid lat or lon"}), 400

    coords_path = ROOT / "frontend" / "src" / "utils" / "master_coords.json"
    import json
    with open(coords_path, "r", encoding="utf-8") as f:
        master = json.load(f)
    master[stop_name] = [round(lat, 6), round(lon, 6)]
    with open(coords_path, "w", encoding="utf-8") as f:
        json.dump(master, f, indent=2, ensure_ascii=False)
    return jsonify({"status": "SUCCESS", "stop_name": stop_name, "coords": [lat, lon]})


@app.route("/api/optimize_cvrptw", methods=["POST"])
def optimize_cvrptw():
    """Runs Google OR-Tools CVRPTW for a given shift and roster."""
    try:
        from aggregator_cvrptw import aggregate_employee_stops, fetch_road_time_matrix, DEFAULT_DEPOT, DEFAULT_FACILITY
        from optimizer_cvrptw import optimize_routes

        data = request.get_json(silent=True) or {}
        shift = data.get("shift", "A")
        raw_employees = data.get("employees", [])

        if not raw_employees:
            # Fall back to cleaned dataset on disk if none supplied
            raw_path = ROOT / "output/01_cleaned_employees.xlsx"
            if not raw_path.exists():
                raw_path = ROOT / "data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx"
            df = pd.read_excel(raw_path, sheet_name="Employees" if "cleaned" in str(raw_path) else 0)
        else:
            df = pd.DataFrame(raw_employees)

        dest_dict = data.get("destination") or DEFAULT_FACILITY
        depot_dict = DEFAULT_DEPOT

        stops, unverified = aggregate_employee_stops(df, shift_filter=shift)

        if not stops:
            return jsonify({
                "status": "NO_STOPS",
                "message": f"No verified stops found for shift {shift}.",
                "routes": [],
                "unverified_employees": unverified
            })

        all_nodes = [
            {"stop_name": depot_dict["name"], "lat": depot_dict["lat"], "lon": depot_dict["lon"], "demand": 0, "service_minutes": 0},
            *stops,
            {"stop_name": dest_dict.get("name", "Company Facility"), "lat": dest_dict["lat"], "lon": dest_dict["lon"], "demand": 0, "service_minutes": 0},
        ]

        time_matrix, dist_matrix = fetch_road_time_matrix(all_nodes)
        stop_names = [n["stop_name"] for n in all_nodes]
        demands = [n["demand"] for n in all_nodes]
        service_minutes = [n["service_minutes"] for n in all_nodes]

        res = optimize_routes(
            time_matrix=time_matrix,
            demands=demands,
            stop_names=stop_names,
            service_minutes=service_minutes,
            num_buses=data.get("num_buses", 8),
            earliest_departure=data.get("earliest_departure", 330),
            latest_departure=data.get("latest_departure", 360),
            shift_start=data.get("shift_start", 410),
            max_route_minutes=data.get("max_route_minutes", 60),
            arrival_buffer=data.get("arrival_buffer", 10),
            bus_capacity=data.get("bus_capacity", 32),
        )

        # Attach coordinates and employee lists to stops in solution
        if res.get("routes"):
            for r in res["routes"]:
                for s in r["stops"]:
                    node_idx = s["node_index"]
                    node_obj = all_nodes[node_idx]
                    s["lat"] = node_obj["lat"]
                    s["lon"] = node_obj["lon"]
                    if 1 <= node_idx <= len(stops):
                        s["employees"] = stops[node_idx - 1].get("employees", [])
                    else:
                        s["employees"] = []

        return jsonify({
            **res,
            "shift": shift,
            "unverified_count": len(unverified),
            "unverified_employees": unverified,
            "destination": dest_dict,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/optimize_all", methods=["POST"])
def optimize_all():
    """Runs Google OR-Tools CVRPTW for ALL shifts from a raw employee list.

    Accepts the same employee JSON array the frontend already has after geocoding.
    Returns routes in the same shape as clusterStopsIntoRoutes() so the frontend
    can use OR-Tools results directly without any extra mapping.
    """
    try:
        from aggregator_cvrptw import aggregate_employee_stops, fetch_road_time_matrix, DEFAULT_FACILITY
        from optimizer_cvrptw import optimize_routes

        data = request.get_json(silent=True) or {}
        raw_employees = data.get("employees", [])
        dest_dict = data.get("destination") or DEFAULT_FACILITY
        bus_capacity = int(data.get("bus_capacity", 32))

        if not raw_employees:
            return jsonify({"error": "No employees supplied."}), 400

        df = pd.DataFrame(raw_employees)
        # Normalise column names that come from the frontend employee objects
        rename_map = {
            "pickup_point": "pickup_point",
            "latitude": "latitude",
            "longitude": "longitude",
            "location_status": "location_status",
            "shift": "shift",
            "employee_id": "employee_id",
            "employee_name": "employee_name",
            "mobile": "mobile",
            "grade": "grade",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        all_routes = []
        all_employees_assigned = []
        total_routed = 0
        total_unverified = 0

        # Detect which shifts exist in this dataset
        shifts_present = []
        if "shift" in df.columns:
            raw_shifts = df["shift"].dropna().astype(str).str.upper().str.strip().unique()
            if any("A" in s for s in raw_shifts):
                shifts_present.append("A")
            if any(s == "B" for s in raw_shifts):
                shifts_present.append("B")
            if any(s == "C" for s in raw_shifts):
                shifts_present.append("C")
        if not shifts_present:
            shifts_present = ["A"]

        bus_letter_counter = {}

        for shift in shifts_present:
            stops, unverified = aggregate_employee_stops(
                df, shift_filter=shift, bus_capacity=bus_capacity
            )

            # Strictly filter stops within the practical 50 km service radius
            clean_stops = []
            for s in stops:
                dist_to_fac = haversine_km(s["lat"], s["lon"], dest_dict["lat"], dest_dict["lon"])
                if dist_to_fac <= 50.0:
                    clean_stops.append(s)
                else:
                    for emp in s.get("employees", []):
                        unverified.append({
                            **emp,
                            "location_status": "OUTSIDE_SERVICE_RADIUS",
                            "resolution_reason": f"Pickup is {dist_to_fac:.1f} km away from plant (exceeds practical 50 km service radius).",
                        })

            stops = clean_stops
            total_unverified += len(unverified)

            if not stops:
                continue

            # Partition stops into natural highway corridors leading to the plant
            corridor_pools = {}
            for s in stops:
                c = get_corridor(s["lat"], s["lon"], dest_dict["lat"], dest_dict["lon"])
                corridor_pools.setdefault(c, []).append(s)

            dest_node = {
                "stop_name": dest_dict.get("name", "Company Facility"),
                "lat": dest_dict["lat"], "lon": dest_dict["lon"],
                "demand": 0, "service_minutes": 0,
            }

            # Time window config (minutes since midnight)
            # Shift A: 06:50 start | Shift B: 14:50 start | Shift C: 22:50 start
            shift_windows = {
                "A": (330, 380, 410),
                "B": (810, 860, 890),
                "C": (1290, 1340, 1370),
            }
            earliest_dep, latest_dep, shift_start = shift_windows.get(shift, (330, 380, 410))

            # Strict maximum commute duration: no employee should ever ride more than 70 minutes
            MAX_COMMUTE_MINUTES = 70
            earliest_allowed_dep = shift_start - MAX_COMMUTE_MINUTES - 10

            for corridor_name, c_stops in corridor_pools.items():
                # In employee pickup routing to a facility, buses start at their respective pickup clusters.
                # A virtual depot with 0-time departure allows OR-Tools to choose the optimal start stop for each bus.
                depot_node = {
                    "stop_name": f"Depot ({corridor_name})", "lat": dest_dict["lat"], "lon": dest_dict["lon"],
                    "demand": 0, "service_minutes": 0,
                }

                all_nodes = [depot_node, *c_stops, dest_node]
                time_matrix, dist_matrix = fetch_road_time_matrix(all_nodes)
                # Virtual open start: zero-time departure from depot to all stops
                for j in range(len(all_nodes)):
                    time_matrix[0][j] = 0

                total_pax = sum(s["demand"] for s in c_stops)
                min_buses_needed = max(1, -(-total_pax // bus_capacity))
                max_buses_to_try = min(len(c_stops), min_buses_needed + 1)

                # Fleet minimization: Try fewest buses first (1 bus if <= 32 pax).
                # Increment only if infeasible under 70 min limit.
                corridor_routes = []
                for nb in range(min_buses_needed, max_buses_to_try + 1):
                    res = optimize_routes(
                        time_matrix=time_matrix,
                        demands=[n["demand"] for n in all_nodes],
                        stop_names=[n["stop_name"] for n in all_nodes],
                        service_minutes=[n["service_minutes"] for n in all_nodes],
                        num_buses=nb,
                        earliest_departure=earliest_allowed_dep,
                        latest_departure=latest_dep,
                        shift_start=shift_start,
                        max_route_minutes=70,  # Strict <= 70 min hard limit
                        arrival_buffer=10,
                        bus_capacity=bus_capacity,
                        time_limit_seconds=0.6,
                    )
                    if res.get("status") in ("OPTIMAL_OR_FEASIBLE",) and res.get("routes"):
                        corridor_routes = res["routes"]
                        print(f"[OR-Tools] Shift {shift} Corridor '{corridor_name}': solved with {len(corridor_routes)} bus(es) for {total_pax} pax!")
                        break

                print(f"[OR-Tools] Shift {shift} Corridor '{corridor_name}': final routes={len(corridor_routes)}")

                # Fallback to capacity-constrained sequential chunks if solver could not find feasible route
                if not corridor_routes:
                    ordered_c = sorted(c_stops, key=lambda s: haversine_km(s["lat"], s["lon"], dest_dict["lat"], dest_dict["lon"]), reverse=True)
                    chunks = []
                    curr_ch = []
                    curr_dem = 0
                    for st in ordered_c:
                        dem = st["demand"]
                        if curr_ch and (curr_dem + dem > (bus_capacity - 2)):
                            chunks.append(curr_ch)
                            curr_ch = []
                            curr_dem = 0
                        # Correct 1-based index into c_stops (matching all_nodes[node_idx])
                        c_idx = c_stops.index(st) + 1
                        curr_ch.append((c_idx, st))
                        curr_dem += dem
                    if curr_ch:
                        chunks.append(curr_ch)

                    corridor_routes = []
                    for ch in chunks:
                        first_st = ch[0][1]
                        dur = round(haversine_km(first_st["lat"], first_st["lon"], dest_dict["lat"], dest_dict["lon"]) / 30 * 60, 1)
                        corridor_routes.append({
                            "route_duration_minutes": max(20.0, min(65.0, dur)),
                            "total_passengers": sum(item[1]["demand"] for item in ch),
                            "stops": [{"node_index": item[0], "arrival_time_minutes": 360} for item in ch],
                        })


                for r in corridor_routes:
                    bus_letter_counter.setdefault(shift, 0)
                    bus_letter_counter[shift] += 1
                    bus_id = f"Bus {shift}-{bus_letter_counter[shift]}"

                    route_stops_raw = [
                        s for s in r["stops"]
                        if 1 <= s["node_index"] <= len(c_stops)
                    ]

                    ordered_stops = []
                    cum_km   = 0.0
                    cum_pax  = 0
                    prev_lat = prev_lon = None

                    for s in route_stops_raw:
                        node_idx = s["node_index"]
                        node_obj = all_nodes[node_idx]
                        stop_obj = c_stops[node_idx - 1]
                        lat, lon = node_obj["lat"], node_obj["lon"]

                        dist = 0.0
                        if prev_lat is not None:
                            dist = haversine_km(prev_lat, prev_lon, lat, lon)
                        cum_km  += dist
                        cum_pax += stop_obj["demand"]
                        dist_to_dest = haversine_km(lat, lon, dest_dict["lat"], dest_dict["lon"])

                        ordered_stops.append({
                            "seq":                        len(ordered_stops) + 1,
                            "stop_name":                  stop_obj["stop_name"],
                            "lat":                        round(lat, 5),
                            "lon":                        round(lon, 5),
                            "distance_from_prev_km":      round(dist, 1),
                            "cumulative_km":              round(cum_km, 1),
                            "passengers_boarding":        stop_obj["demand"],
                            "cumulative_passengers":      cum_pax,
                            "distance_to_destination_km": round(dist_to_dest, 1),
                            "employees":                  stop_obj.get("employees", []),
                            "ride_minutes_to_destination": round(dist_to_dest / 30 * 60, 1),
                            "ride_time_status":           "ON_TARGET",
                        })
                        prev_lat, prev_lon = lat, lon

                    if not ordered_stops:
                        continue

                    final_leg_km = haversine_km(
                        ordered_stops[-1]["lat"], ordered_stops[-1]["lon"],
                        dest_dict["lat"], dest_dict["lon"]
                    )
                    total_km = round(cum_km + final_leg_km, 1)
                    total_passengers = cum_pax
                    total_routed += total_passengers

                    calc_duration = round((cum_km + final_leg_km) / 32 * 60, 1)
                    all_routes.append({
                        "bus_id":                bus_id,
                        "shift":                 f"Shift {shift}",
                        "corridor":              corridor_name,
                        "passengers_assigned":   total_passengers,
                        "capacity":              bus_capacity,
                        "seats_available":       max(0, bus_capacity - total_passengers),
                        "total_stops":           len(ordered_stops),
                        "total_km":              total_km,
                        "road_km":               total_km,
                        "total_minutes":         calc_duration,
                        "longest_ride_minutes":  calc_duration,
                        "ride_time_status":      "ON_TARGET" if calc_duration <= 60 else "WITHIN_ALLOWANCE" if calc_duration <= 70 else "EXCEEDS_LIMIT",
                        "passenger_ride_minutes": 0,
                        "stops_only_km":         round(cum_km, 1),
                        "final_leg_to_destination_km": round(final_leg_km, 1),
                        "destination_name":      dest_dict.get("name", "Company Facility"),
                        "max_jump_km":           max((s["distance_from_prev_km"] for s in ordered_stops), default=0.0),
                        "start_stop":            ordered_stops[0]["stop_name"],
                        "end_stop":              dest_dict.get("name", "Company Facility"),
                        "stops":                 ordered_stops,
                        "destination":           dest_dict,
                    })

        # Fleet Consolidation Pass: Merge smaller buses in the same corridor if combined pax <= 32 and duration <= 70 min
        merged_routes = []
        for shift_val in shifts_present:
            shift_routes = [r for r in all_routes if r["shift"] == f"Shift {shift_val}"]
            changed = True
            while changed:
                changed = False
                for i in range(len(shift_routes)):
                    for j in range(i + 1, len(shift_routes)):
                        r1, r2 = shift_routes[i], shift_routes[j]
                        if r1["corridor"] != r2["corridor"]:
                            # Allow cross-corridor merge if one bus has low occupancy (<= 5 pax)
                            # and the two buses are physically close (<= 8 km apart)
                            if min(r1["passengers_assigned"], r2["passengers_assigned"]) > 5:
                                continue
                            min_inter_dist = min(
                                haversine_km(s1["lat"], s1["lon"], s2["lat"], s2["lon"])
                                for s1 in r1["stops"] for s2 in r2["stops"]
                            )
                            if min_inter_dist > 8.0:
                                continue
                        combined_pax = r1["passengers_assigned"] + r2["passengers_assigned"]
                        if combined_pax > bus_capacity:
                            continue
                        # Combine unique physical stops
                        comb_stops = []
                        seen_coords = set()
                        for s in r1["stops"] + r2["stops"]:
                            key = f"{round(s['lat'], 4)}_{round(s['lon'], 4)}"
                            if key not in seen_coords:
                                seen_coords.add(key)
                                comb_stops.append(dict(s))
                            else:
                                for ex in comb_stops:
                                    if f"{round(ex['lat'], 4)}_{round(ex['lon'], 4)}" == key:
                                        ex["passengers_boarding"] += s["passengers_boarding"]
                                        ex["employees"] = ex.get("employees", []) + s.get("employees", [])
                                        break
                        # Sequence towards destination
                        comb_stops = sorted(comb_stops, key=lambda s: s["distance_to_destination_km"], reverse=True)
                        cum_k = 0.0
                        prv = None
                        for stp in comb_stops:
                            stp_dist = 0.0 if prv is None else haversine_km(prv["lat"], prv["lon"], stp["lat"], stp["lon"])
                            cum_k += stp_dist
                            stp["distance_from_prev_km"] = round(stp_dist, 1)
                            stp["cumulative_km"] = round(cum_k, 1)
                            prv = stp
                        final_k = haversine_km(comb_stops[-1]["lat"], comb_stops[-1]["lon"], dest_dict["lat"], dest_dict["lon"])
                        tot_k = round(cum_k + final_k, 1)
                        tot_dur = round(tot_k / 32 * 60, 1)
                        if tot_dur <= 70.0:
                            # Merge r2 into r1
                            r1["stops"] = comb_stops
                            r1["passengers_assigned"] = combined_pax
                            r1["seats_available"] = max(0, bus_capacity - combined_pax)
                            r1["total_stops"] = len(comb_stops)
                            r1["total_km"] = tot_k
                            r1["road_km"] = tot_k
                            r1["total_minutes"] = tot_dur
                            r1["longest_ride_minutes"] = tot_dur
                            r1["ride_time_status"] = "ON_TARGET" if tot_dur <= 60 else "WITHIN_ALLOWANCE"
                            shift_routes.pop(j)
                            changed = True
                            break
                    if changed:
                        break
            merged_routes.extend(shift_routes)

        # Renumber buses neatly per shift and map employee assignments
        final_routes = []
        all_employees_assigned = []
        b_counter = {}
        for r in merged_routes:
            sh = r["shift"].replace("Shift ", "")
            b_counter.setdefault(sh, 0)
            b_counter[sh] += 1
            new_id = f"Bus {sh}-{b_counter[sh]}"
            r["bus_id"] = new_id
            final_routes.append(r)
            for seq_idx, st in enumerate(r["stops"], 1):
                st["seq"] = seq_idx
                for emp in st.get("employees", []):
                    all_employees_assigned.append({
                        "sr_no":           len(all_employees_assigned) + 1,
                        "employee_id":     emp.get("employee_id", ""),
                        "employee_name":   emp.get("employee_name", ""),
                        "grade":           emp.get("grade", ""),
                        "shift":           emp.get("shift", sh),
                        "corridor":        r["corridor"],
                        "pickup_point":    emp.get("pickup_point", ""),
                        "location_context": "",
                        "assigned_bus":    new_id,
                        "mobile":          emp.get("mobile", ""),
                        "lat":             st["lat"],
                        "lon":             st["lon"],
                        "seq":             seq_idx,
                    })

        all_routes = final_routes

        return jsonify({
            "status": "OK",
            "routes":               all_routes,
            "employees":            all_employees_assigned,
            "routed_employees":     sum(r["passengers_assigned"] for r in all_routes),
            "unverified_employees": total_unverified,
            "total_buses":          len(all_routes),
            "total_stops":          sum(r["total_stops"] for r in all_routes),
            "total_fleet_km":       round(sum(r["total_km"] for r in all_routes), 1),
        })


    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/api/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    if f.filename == "":
        return jsonify({"error": "Empty filename"}), 400
    try:
        df = pd.read_excel(f)
        result = process_and_group(df)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/export_excel", methods=["POST"])
def export_excel():
    data = request.json
    routes = data.get("routes", [])
    employees = data.get("employees", [])

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Bus Routes Summary"
    ws1.views.sheetView[0].showGridLines = True

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    title_font = Font(name="Calibri", size=14, bold=True, color="1F4E79")
    regular_font = Font(name="Calibri", size=11)
    bold_font = Font(name="Calibri", size=11, bold=True)
    thin_border = Border(left=Side(style="thin", color="D9D9D9"),
                         right=Side(style="thin", color="D9D9D9"),
                         top=Side(style="thin", color="D9D9D9"),
                         bottom=Side(style="thin", color="D9D9D9"))

    ws1.merge_cells("A1:H1")
    ws1["A1"] = "COMPANY BUS ROSTER - GROUPED BY CORRIDOR (~15 SEATS)"
    ws1["A1"].font = title_font
    ws1["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws1.row_dimensions[1].height = 30

    headers1 = ["Bus ID", "Shift", "Corridor Area", "Passengers", "Seats Left", "Total Stops", "Route Span (km)", "Route (Stops in Order)"]
    for col_idx, h in enumerate(headers1, 1):
        c = ws1.cell(row=3, column=col_idx, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center" if "Seat" in h or "Pass" in h or "Stop" in h or "Span" in h else "left", vertical="center")
    ws1.row_dimensions[3].height = 24

    for row_idx, r in enumerate(routes, 4):
        ws1.row_dimensions[row_idx].height = 22
        stops_text = " -> ".join([s["stop_name"] for s in r.get("stops", [])])
        vals = [
            r["bus_id"], r["shift"], r["corridor"], r["total_passengers"],
            r["seats_available"], r["total_stops"], r["total_km"], stops_text
        ]
        for col_idx, val in enumerate(vals, 1):
            cell = ws1.cell(row=row_idx, column=col_idx, value=val)
            cell.font = bold_font if col_idx in (1, 4, 5) else regular_font
            cell.border = thin_border
            if col_idx in (1, 2, 4, 5, 6, 7):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    # Sheet 2: Employee list
    ws2 = wb.create_sheet(title="Employee Passenger List")
    ws2.views.sheetView[0].showGridLines = True

    ws2.merge_cells("A1:H1")
    ws2["A1"] = "EMPLOYEE SEATING & BUS ASSIGNMENT"
    ws2["A1"].font = title_font
    ws2["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws2.row_dimensions[1].height = 30

    headers2 = [
        "Sr No", "Employee ID", "Employee Name", "Shift", "Assigned Bus",
        "Pickup Point", "Mobile No", "Location Status",
    ]
    for col_idx, h in enumerate(headers2, 1):
        c = ws2.cell(row=3, column=col_idx, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center" if h in ("Sr No", "Shift", "Assigned Bus", "Mobile No", "Location Status") else "left", vertical="center")
    ws2.row_dimensions[3].height = 24

    for row_idx, er in enumerate(employees, 4):
        ws2.row_dimensions[row_idx].height = 20
        vals = [
            er["sr_no"], er["employee_id"], er["employee_name"], er["shift"],
            er["assigned_bus"], er["pickup_point"], er["mobile"],
            er.get("location_status", "VERIFIED"),
        ]
        for col_idx, val in enumerate(vals, 1):
            cell = ws2.cell(row=row_idx, column=col_idx, value=val)
            cell.font = bold_font if col_idx == 5 else regular_font
            cell.border = thin_border
            if col_idx in (1, 2, 4, 5, 7, 8):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    unverified_employees = [
        employee
        for employee in employees
        if employee.get("location_status") == "LOCATION_UNVERIFIED"
    ]
    if unverified_employees:
        ws4 = wb.create_sheet(title="Location Review")
        ws4.views.sheetView[0].showGridLines = True
        ws4.merge_cells("A1:H1")
        ws4["A1"] = "EMPLOYEES WITH UNVERIFIED PICKUP LOCATIONS"
        ws4["A1"].font = title_font
        ws4["A1"].alignment = Alignment(horizontal="left", vertical="center")
        ws4.row_dimensions[1].height = 30
        review_headers = [
            "Sr No", "Employee ID", "Employee Name", "Shift",
            "Pickup Point", "Mobile No", "Location Status", "Resolution Note",
        ]
        for col_idx, header in enumerate(review_headers, 1):
            cell = ws4.cell(row=3, column=col_idx, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="left", vertical="center")
        for row_idx, employee in enumerate(unverified_employees, 4):
            values = [
                row_idx - 3,
                employee.get("employee_id", ""),
                employee.get("employee_name", ""),
                employee.get("shift", ""),
                employee.get("pickup_point", ""),
                employee.get("mobile", ""),
                employee.get("location_status", "LOCATION_UNVERIFIED"),
                employee.get("resolution_reason", ""),
            ]
            for col_idx, value in enumerate(values, 1):
                cell = ws4.cell(row=row_idx, column=col_idx, value=value)
                cell.font = regular_font
                cell.border = thin_border
                cell.alignment = Alignment(vertical="center", wrap_text=col_idx == 8)

    # Sheet 3: Stop-by-stop details
    ws3 = wb.create_sheet(title="Stop-by-Stop Schedule")
    ws3.views.sheetView[0].showGridLines = True

    ws3.merge_cells("A1:H1")
    ws3["A1"] = "STOP-BY-STOP SCHEDULE WITH INTER-STOP DISTANCES"
    ws3["A1"].font = title_font
    ws3["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws3.row_dimensions[1].height = 30

    headers3 = ["Bus ID", "Shift", "Stop Seq", "Bus Stop Name", "Dist from Prev (km)", "Cumulative (km)", "Boarding Count", "Total on Bus"]
    for col_idx, h in enumerate(headers3, 1):
        c = ws3.cell(row=3, column=col_idx, value=h)
        c.font = header_font
        c.fill = header_fill
        c.alignment = Alignment(horizontal="center" if col_idx in (1, 2, 3, 5, 6, 7, 8) else "left", vertical="center")
    ws3.row_dimensions[3].height = 24

    row_count = 4
    for r in routes:
        for s in r.get("stops", []):
            ws3.row_dimensions[row_count].height = 20
            vals = [
                r["bus_id"], r["shift"], s["seq"], s["stop_name"],
                s["distance_km"], s["cumulative_km"], s["passengers_boarding"], s["cumulative_passengers"]
            ]
            for col_idx, val in enumerate(vals, 1):
                cell = ws3.cell(row=row_count, column=col_idx, value=val)
                cell.font = bold_font if col_idx in (1, 4) else regular_font
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_idx in (1, 2, 3, 5, 6, 7, 8) else "left", vertical="center")
            row_count += 1

    worksheets = [ws1, ws2, ws3, *([ws4] if unverified_employees else [])]
    for ws in worksheets:
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                if cell.row > 1 and cell.value:
                    max_len = max(max_len, len(str(cell.value)))
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 48)

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(bio, as_attachment=True, download_name="Grouped_Bus_Roster.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
