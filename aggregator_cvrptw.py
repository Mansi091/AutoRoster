DEFAULT_FACILITY = {"name": "JCB India Ltd", "lat": 18.7765871, "lon": 73.6669931}

"""Pickup Aggregator and Road Travel-Time Matrix Builder for CVRPTW.

Step 2 & Step 3 Implementation:
- Step 2: Aggregates cleaned employee rows into unique physical pickup stops with verified coordinates & demand.
- Step 3: Constructs the road travel-time matrix (in integer minutes) and distance matrix using
  OSRM table routing between all pairs:
  [Node 0: Bus Depot/Start] -> [Node 1 ... n-2: Unique Stops] -> [Node n-1: Workplace Facility].
"""

import json
import logging
import math
import os
import re
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

# Load facility, depot, and parameters from settings.json
def _load_settings():
    settings_file = ROOT / "settings.json"
    if settings_file.exists():
        with open(settings_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "facility": {"name": "JCB India Ltd, Talegaon", "latitude": 18.7765871, "longitude": 73.6947478},
        "depot": {"name": "Central Bus Depot", "latitude": 18.7300, "longitude": 73.6800},
        "bus_capacity": 32,
    }

_SETTINGS = _load_settings()

def get_facility() -> Dict[str, Any]:
    f = _SETTINGS.get("facility", {})
    return {"name": f.get("name", "Facility"), "lat": f.get("latitude", 18.7765871), "lon": f.get("longitude", 73.6947478)}

def get_depot() -> Dict[str, Any]:
    d = _SETTINGS.get("depot") or {}
    return {"name": d.get("name", "Central Bus Depot"), "lat": d.get("latitude", 18.7300), "lon": d.get("longitude", 73.6800)}

OSRM_TABLE_URL = _SETTINGS.get("osrm_url", "https://router.project-osrm.org").rstrip("/") + "/table/v1/driving"
_MATRIX_CACHE: Dict[str, Tuple[List[List[int]], List[List[float]]]] = {}

def get_primary_shift(val: Any, priority: List[str] = ["A", "B", "C"]) -> str:
    """Earliest configured shift resolution (e.g., 'A+B' -> 'A')."""
    tokens = [t for t in re.split(r"[\s+,&/]+", str(val).upper()) if t]
    for p in priority:
        if p in tokens:
            return p
    return "A"

def aggregate_employee_stops(
    employees_df: pd.DataFrame,
    shift_filter: Optional[str] = "A",
    bus_capacity: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Aggregates individual employee rows into unique pickup stops.

    Handles large pickup groups by splitting any stop with demand > bus_capacity
    into multiple manageable units of size <= bus_capacity.
    """
    cap = bus_capacity or int(_SETTINGS.get("bus_capacity", 32))
    df = employees_df.copy()
    if shift_filter and "shift" in df.columns:
        df["primary_shift"] = df["shift"].apply(lambda v: get_primary_shift(v))
        df = df[df["primary_shift"] == shift_filter.upper()]

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

        coord_key = f"{round(lat, 4)}_{round(lon, 4)}"

        if coord_key not in stops_grouped:
            stops_grouped[coord_key] = {
                "stop_name": canonical_name,
                "lat": float(lat),
                "lon": float(lon),
                "employees": [],
            }
        stops_grouped[coord_key]["employees"].append(emp_dict)

    final_stops: List[Dict[str, Any]] = []

    for key, data in stops_grouped.items():
        total_pax = len(data["employees"])
        if total_pax <= cap:
            final_stops.append({
                "stop_name": data["stop_name"],
                "lat": data["lat"],
                "lon": data["lon"],
                "demand": total_pax,
                "service_minutes": max(1, math.ceil(total_pax * 0.25)),
                "employees": data["employees"],
            })
        else:
            emps = data["employees"]
            for chunk_idx, i in enumerate(range(0, total_pax, cap), 1):
                chunk = emps[i:i + cap]
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
    timeout_sec: int = 20,
) -> Tuple[List[List[int]], List[List[float]]]:
    """Builds the N x N road travel time matrix (integer minutes) and distance matrix (km)."""
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
                            km = round(metres / 1000.0, 2) if metres is not None else round(mins * 0.5, 2)
                        else:
                            h_km = haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
                            km = round(h_km * float(_SETTINGS.get("fallback_road_factor", 1.45)), 2)
                            mins = max(1, math.ceil(km / float(_SETTINGS.get("fallback_speed_kmph", 28.0)) * 60))

                        t_row.append(int(mins))
                        d_row.append(float(km))
                time_matrix.append(t_row)
                dist_matrix.append(d_row)

            _MATRIX_CACHE[coords_str] = (time_matrix, dist_matrix)
            return time_matrix, dist_matrix

    except Exception as e:
        LOGGER.warning(f"OSRM Table API fetch failed ({e}); using calibrated road network model.")

    time_matrix = []
    dist_matrix = []
    road_factor = float(_SETTINGS.get("fallback_road_factor", 1.45))
    speed = float(_SETTINGS.get("fallback_speed_kmph", 28.0))

    for i in range(n):
        t_row = []
        d_row = []
        for j in range(n):
            if i == j:
                t_row.append(0)
                d_row.append(0.0)
            else:
                straight_km = haversine_km(nodes[i]["lat"], nodes[i]["lon"], nodes[j]["lat"], nodes[j]["lon"])
                road_km = round(straight_km * road_factor, 2)
                mins = max(1, math.ceil(road_km / speed * 60))
                t_row.append(int(mins))
                d_row.append(float(road_km))
        time_matrix.append(t_row)
        dist_matrix.append(d_row)

    return time_matrix, dist_matrix

def prepare_cvrptw_inputs_for_shift(
    excel_path: Path,
    shift: str = "A",
    sheet_name: Optional[str] = None,
    depot: Optional[Dict[str, Any]] = None,
    facility: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Reads employee roster, groups by stop, attaches depot & facility, and returns CVRPTW payload."""
    fac = facility or get_facility()
    dep = depot or get_depot()
    target_sheet = sheet_name or (0 if "MAIN" in str(excel_path) else "Employees")

    df = pd.read_excel(excel_path, sheet_name=target_sheet)
    stops, unverified = aggregate_employee_stops(df, shift_filter=shift)

    all_nodes = [
        {"stop_name": dep["name"], "lat": dep["lat"], "lon": dep["lon"], "demand": 0, "service_minutes": 0, "employees": []},
        *stops,
        {"stop_name": fac["name"], "lat": fac["lat"], "lon": fac["lon"], "demand": 0, "service_minutes": 0, "employees": []},
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
