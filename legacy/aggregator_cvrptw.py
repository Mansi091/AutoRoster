"""Pickup Aggregator and Road Travel-Time Matrix Builder for CVRPTW.

Step 2 & Step 3 Implementation:
- Step 2: Aggregates cleaned employee rows into unique physical pickup stops with verified coordinates & demand.
- Step 3: Constructs the road travel-time matrix (in integer minutes) and distance matrix using
  OSRM / Geoapify table routing between all pairs:
  [Node 0: Bus Depot/Start] -> [Node 1 ... n-2: Unique Stops] -> [Node n-1: Workplace Facility].
"""

import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
import requests
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from geocoding import resolve_pickup_points
from utils import haversine_km

LOGGER = logging.getLogger(__name__)

# Drop point facility: JCB India Ltd (18.7765871, 73.6947478)
DEFAULT_FACILITY = {
    "name": "JCB India Ltd, Talegaon",
    "lat": 18.7765871,
    "lon": 73.6947478,
}

# Default fleet depot origin (Talegaon / Pune depot hub)
DEFAULT_DEPOT = {
    "name": "Central Bus Depot",
    "lat": 18.7300,
    "lon": 73.6800,
}

OSRM_TABLE_URL = "https://router.project-osrm.org/table/v1/driving"
_MATRIX_CACHE: Dict[str, Tuple[List[List[int]], List[List[float]]]] = {}


def aggregate_employee_stops(
    employees_df: pd.DataFrame,
    shift_filter: Optional[str] = "A",
    bus_capacity: int = 32
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Aggregates individual employee rows into unique pickup stops.

    Handles large pickup groups by splitting any stop with demand > bus_capacity
    into multiple manageable units of size <= bus_capacity.

    Returns:
        (aggregated_stops, unverified_employees)
    """
    df = employees_df.copy()
    if shift_filter and "shift" in df.columns:
        df = df[df["shift"].astype(str).str.upper().str.contains(shift_filter, na=False)]

    unique_pickup_names = [p for p in df["pickup_point"].dropna().unique() if str(p).strip()]
    resolved_locations = resolve_pickup_points(unique_pickup_names)

    stops_grouped: Dict[str, Dict[str, Any]] = {}
    unverified: List[Dict[str, Any]] = []

    for _, row in df.iterrows():
        p_name = str(row.get("pickup_point", "")).strip()
        emp_dict = {
            "employee_id": str(row.get("employee_id", "")),
            "employee_name": str(row.get("employee_name", "")),
            "shift": str(row.get("shift", "")),
            "pickup_point": p_name,
            "mobile": str(row.get("mobile", "")),
        }

        loc_info = resolved_locations.get(p_name)
        if not loc_info or loc_info.get("location_status") != "VERIFIED":
            unverified.append({
                **emp_dict,
                "reason": loc_info.get("resolution_reason", "Unverified coordinate") if loc_info else "Not resolved"
            })
            continue

        lat = loc_info["latitude"]
        lon = loc_info["longitude"]
        canonical_name = loc_info.get("canonical_name", p_name)

        # Key by canonical name & rounded coordinates to merge duplicate spelling variants
        coord_key = f"{round(lat, 4)}_{round(lon, 4)}"

        if coord_key not in stops_grouped:
            stops_grouped[coord_key] = {
                "stop_name": canonical_name,
                "lat": float(lat),
                "lon": float(lon),
                "employees": [],
            }
        stops_grouped[coord_key]["employees"].append(emp_dict)

    # Process demands and split any oversized stops if count > bus_capacity
    final_stops: List[Dict[str, Any]] = []

    for key, data in stops_grouped.items():
        total_pax = len(data["employees"])
        if total_pax <= bus_capacity:
            final_stops.append({
                "stop_name": data["stop_name"],
                "lat": data["lat"],
                "lon": data["lon"],
                "demand": total_pax,
                "service_minutes": max(1, math.ceil(total_pax * 0.25)),  # dwell time ~15 sec per passenger
                "employees": data["employees"],
            })
        else:
            # Split into chunks of size bus_capacity
            emps = data["employees"]
            for chunk_idx, i in enumerate(range(0, total_pax, bus_capacity), 1):
                chunk = emps[i:i + bus_capacity]
                final_stops.append({
                    "stop_name": f"{data['stop_name']} (Group {chunk_idx})",
                    "lat": data["lat"],
                    "lon": data["lon"],
                    "demand": len(chunk),
                    "service_minutes": max(1, math.ceil(len(chunk) * 0.25)),
                    "employees": chunk,
                })

    return final_stops, unverified


def fetch_road_time_matrix(
    nodes: List[Dict[str, Any]],
    timeout_sec: int = 20
) -> Tuple[List[List[int]], List[List[float]]]:
    """Builds the N x N road travel time matrix (integer minutes) and distance matrix (km).

    Uses OSRM Table API with automated Haversine road-factor fallback for robustness.

    Args:
        nodes: Ordered list of nodes, where each dict has 'lat' and 'lon'.

    Returns:
        (time_matrix_minutes, distance_matrix_km)
    """
    n = len(nodes)
    if n == 0:
        return [], []

    coords_str = ";".join(f"{node['lon']:.5f},{node['lat']:.5f}" for node in nodes)
    if coords_str in _MATRIX_CACHE:
        return _MATRIX_CACHE[coords_str]

    url = f"{OSRM_TABLE_URL}/{coords_str}?annotations=duration,distance"

    try:
        resp = requests.get(url, timeout=timeout_sec)
        data = resp.json()

        if data.get("code") == "Ok" and "durations" in data:
            durations_raw = data["durations"]
            distances_raw = data.get("distances", [])

            time_matrix = []
            dist_matrix = []

            for i in range(n):
                t_row = []
                d_row = []
                for j in range(n):
                    if i == j:
                        t_row.append(0)
                        d_row.append(0.0)
                    else:
                        sec = durations_raw[i][j]
                        metres = distances_raw[i][j] if distances_raw else None

                        if sec is not None and not math.isnan(sec):
                            mins = max(1, math.ceil(sec / 60.0))
                            km = round(metres / 1000.0, 2) if metres else round(mins * 0.5, 2)
                        else:
                            # Fallback if OSRM returned null for disconnected node
                            h_km = haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
                            km = round(h_km * 1.35, 2)
                            mins = max(1, math.ceil(km / 35.0 * 60))

                        t_row.append(int(mins))
                        d_row.append(float(km))
                time_matrix.append(t_row)
                dist_matrix.append(d_row)

            _MATRIX_CACHE[coords_str] = (time_matrix, dist_matrix)
            return time_matrix, dist_matrix

    except Exception as e:
        LOGGER.warning(f"OSRM Table API fetch failed ({e}); using calibrated road network model.")

    # Calibrated fallback based on Pune road curvature factor (1.4x straight line @ 32 km/h avg speed)
    time_matrix = []
    dist_matrix = []
    for i in range(n):
        t_row = []
        d_row = []
        for j in range(n):
            if i == j:
                t_row.append(0)
                d_row.append(0.0)
            else:
                straight_km = haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
                road_km = round(straight_km * 1.4, 2)
                mins = max(1, math.ceil(road_km / 32.0 * 60))
                t_row.append(int(mins))
                d_row.append(float(road_km))
        time_matrix.append(t_row)
        dist_matrix.append(d_row)

    return time_matrix, dist_matrix


def prepare_cvrptw_inputs_for_shift(
    cleaned_excel_path: Path,
    shift: str = "A",
    depot: Dict[str, Any] = DEFAULT_DEPOT,
    facility: Dict[str, Any] = DEFAULT_FACILITY,
    max_stops: Optional[int] = None
) -> Dict[str, Any]:
    """Full Step 2 & Step 3 pipeline:

    Reads cleaned employee roster, groups by stop, attaches depot & facility,
    and returns complete CVRPTW input payload.
    """
    df = pd.read_excel(cleaned_excel_path, sheet_name="Employees" if "cleaned" in str(cleaned_excel_path) else 0)
    stops, unverified = aggregate_employee_stops(df, shift_filter=shift)

    if max_stops and len(stops) > max_stops:
        stops = stops[:max_stops]

    # Full node list ordered exactly as:
    # 0 = Depot
    # 1 ... n-2 = Pickup stops
    # n-1 = Workplace Facility
    all_nodes = [
        {"stop_name": depot["name"], "lat": depot["lat"], "lon": depot["lon"], "demand": 0, "service_minutes": 0, "employees": []},
        *stops,
        {"stop_name": facility["name"], "lat": facility["lat"], "lon": facility["lon"], "demand": 0, "service_minutes": 0, "employees": []},
    ]

    time_matrix, dist_matrix = fetch_road_time_matrix(all_nodes)

    stop_names = [n["stop_name"] for n in all_nodes]
    demands = [n["demand"] for n in all_nodes]
    service_minutes = [n["service_minutes"] for n in all_nodes]

    return {
        "shift": shift,
        "total_stops": len(stops),
        "total_employees": sum(demands),
        "unverified_count": len(unverified),
        "unverified_employees": unverified,
        "nodes": all_nodes,
        "stop_names": stop_names,
        "demands": demands,
        "service_minutes": service_minutes,
        "time_matrix": time_matrix,
        "distance_matrix": dist_matrix,
    }


if __name__ == "__main__":
    import pprint
    data_file = ROOT / "output/01_cleaned_employees.xlsx"
    if not data_file.exists():
        data_file = ROOT / "data/input/MAIN_SHEET_TWO_RAW_DATA.xlsx"

    print(f"Reading roster from {data_file} for Shift A...")
    prepared = prepare_cvrptw_inputs_for_shift(data_file, shift="A", max_stops=10)

    print("\n--- STEP 2: Aggregated Stops Summary ---")
    print(f"Total stops: {prepared['total_stops']}, Total routed employees: {prepared['total_employees']}")
    print("Sample Nodes & Demands:")
    for i, name in enumerate(prepared['stop_names']):
        print(f"  Node {i:2d}: {name:<45} (Demand: {prepared['demands'][i]:2d} emps, Service: {prepared['service_minutes'][i]} min)")

    print("\n--- STEP 3: Road Travel-Time Matrix (minutes) ---")
    print("Header:", [f"N{i}" for i in range(len(prepared['nodes']))])
    for row_idx, row in enumerate(prepared['time_matrix']):
        print(f"Node {row_idx:2d}:", [f"{val:2d}m" for val in row])
