"""Plan Replay and Evaluation Tool (scripts/evaluate_plan.py).

Takes any plan JSON (stop sequences by stop name) and evaluates each bus
under the CURRENT route_duration_sec, capacity, and commute limits.
Reports pax, Table duration, Route API duration, and feasibility (YES/NO).

RULE: No literal limits or hardcoded coordinates in logic. All configuration
is read dynamically from backend/app/core/config.py.
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from backend.app.core.config import config
from run_route_planner import (
    load_all_verified_stops,
    get_road_matrix,
    fetch_osrm_route_geometry,
    route_duration_sec,
    _stop_dwell_seconds,
    _primary_shift,
    _stop_key,
    BUS_CAPACITY,
    MAX_RIDE_TIME_SEC,
    MAX_RIDE_TIME_MIN,
    TARGET_RIDE_TIME_SEC,
    TARGET_RIDE_TIME_MIN,
    FEASIBILITY_SOURCE,
)


def get_facility() -> Dict[str, Any]:
    lat = getattr(config.FACILITY, "lat", None)
    lon = getattr(config.FACILITY, "lon", None)
    name = getattr(config.FACILITY, "name", "Drop-off Facility")
    if lat is None or lon is None:
        raise ValueError("Drop-off facility coordinates must be provided in config (FACILITY_LAT, FACILITY_LON).")
    return {"name": name, "lat": float(lat), "lon": float(lon)}


def evaluate_stop_sequences(
    bus_routes: Dict[str, List[str]],
    roster_path: Path,
    shift: str = "A",
    facility: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    facility = facility or get_facility()
    stop_lookup = load_all_verified_stops()

    # Read roster to get employee counts per stop for this shift
    df = pd.read_excel(roster_path, header=1).dropna(how="all").reset_index(drop=True)
    shift_col = next((c for c in df.columns if "shift" in str(c).lower()), "Shift")
    stop_col = next((c for c in df.columns if "bus stop" in str(c).lower() or "pickup" in str(c).lower()), "Bus Stop")

    df_shift = df[df[shift_col].map(lambda s: _primary_shift(str(s))) == shift]
    stop_demand: Dict[str, int] = {}
    for _, r in df_shift.iterrows():
        raw_s = str(r[stop_col]).strip()
        stop_demand[raw_s] = stop_demand.get(raw_s, 0) + 1
        key = _stop_key(raw_s)
        stop_demand[key] = stop_demand.get(key, 0) + 1

    print("=" * 85)
    print(f"PLAN EVALUATION REPORT: Shift {shift}")
    print(f"Roster: {roster_path.name} | Facility: {facility['name']} ({facility['lat']:.6f}, {facility['lon']:.6f})")
    print(f"Limits: BUS_CAPACITY={BUS_CAPACITY}, HARD_LIMIT={MAX_RIDE_TIME_MIN:g}m ({MAX_RIDE_TIME_SEC}s), TARGET={TARGET_RIDE_TIME_MIN:g}m ({TARGET_RIDE_TIME_SEC}s)")
    print(f"FEASIBILITY_SOURCE: {FEASIBILITY_SOURCE}")
    print("=" * 85)

    # Resolve all unique stops across all buses
    unique_stop_names = list(dict.fromkeys([s for r_stops in bus_routes.values() for s in r_stops]))
    nodes = []
    node_index_by_name = {}

    for idx, s_name in enumerate(unique_stop_names):
        key = _stop_key(s_name)
        loc = stop_lookup.get(key)
        if not loc:
            # Try fuzzy match or exact raw lookup
            loc = stop_lookup.get(s_name)
        if not loc:
            raise ValueError(f"Stop '{s_name}' could not be resolved from verified stops database.")
        demand = stop_demand.get(s_name, stop_demand.get(key, 1))
        node_item = {
            "name": loc["name"],
            "lat": float(loc["lat"]),
            "lon": float(loc["lon"]),
            "demand": demand,
        }
        nodes.append(node_item)
        node_index_by_name[s_name] = idx
        node_index_by_name[loc["name"]] = idx
        node_index_by_name[key] = idx

    plant_node = len(nodes)
    nodes.append({
        "name": facility["name"],
        "lat": facility["lat"],
        "lon": facility["lon"],
        "demand": 0,
    })

    # Fetch Table road matrix
    time_matrix, dist_matrix = get_road_matrix(nodes)

    route_evaluations = []
    all_feasible = True
    total_km = 0.0

    print(f"{'Bus':<8} | {'Stops':<6} | {'Pax':<8} | {'Table Time':<12} | {'Route API':<12} | {'Gap (s)':<8} | {'Feasible?':<10} | {'Status Reason'}")
    print("-" * 85)

    for bus_id, stop_names in bus_routes.items():
        seq_indices = [node_index_by_name[s] for s in stop_names]
        pax = sum(nodes[i]["demand"] for i in seq_indices)

        # 1. Table API duration
        table_tot_sec, table_drive_sec, table_dwell_sec = route_duration_sec(
            seq_indices, nodes, time_matrix, plant_node, facility, source="table"
        )

        # 2. Route API duration & geometry
        waypoints = [(nodes[i]["lat"], nodes[i]["lon"]) for i in seq_indices] + [(facility["lat"], facility["lon"])]
        geom_info = fetch_osrm_route_geometry(waypoints)
        route_api_tot_sec, route_api_drive_sec, _ = route_duration_sec(
            seq_indices, nodes, time_matrix, plant_node, facility, source="route"
        )
        dist_km = float(geom_info.get("distance_km", 0.0))
        total_km += dist_km

        gap_sec = abs(table_tot_sec - route_api_tot_sec)

        # Check feasibility under CURRENT route_duration_sec (governed by FEASIBILITY_SOURCE)
        authority_sec = table_tot_sec if FEASIBILITY_SOURCE == "table" else route_api_tot_sec
        pax_ok = pax <= BUS_CAPACITY
        time_ok = authority_sec <= MAX_RIDE_TIME_SEC

        is_feasible = pax_ok and time_ok
        if not is_feasible:
            all_feasible = False

        reasons = []
        if not pax_ok:
            reasons.append(f"Pax {pax} > {BUS_CAPACITY}")
        if not time_ok:
            reasons.append(f"Ride {authority_sec/60:.1f}m > {MAX_RIDE_TIME_MIN:g}m ({authority_sec:.0f}s > {MAX_RIDE_TIME_SEC}s)")
        status_reason = ", ".join(reasons) if reasons else ("ON_TARGET" if authority_sec <= TARGET_RIDE_TIME_SEC else "WITHIN_ALLOWANCE")

        route_evaluations.append({
            "bus": bus_id,
            "stops": len(seq_indices),
            "pax": pax,
            "table_sec": round(table_tot_sec, 1),
            "route_api_sec": round(route_api_tot_sec, 1),
            "gap_sec": round(gap_sec, 1),
            "dist_km": dist_km,
            "is_feasible": is_feasible,
            "status_reason": status_reason,
            "stop_indices": seq_indices,
            "stop_names": [nodes[i]["name"] for i in seq_indices],
        })

        print(
            f"{bus_id:<8} | {len(seq_indices):<6} | {pax:<8} | "
            f"{table_tot_sec/60.0:>5.1f}m ({table_tot_sec:>4.0f}s) | "
            f"{route_api_tot_sec/60.0:>5.1f}m ({route_api_tot_sec:>4.0f}s) | "
            f"{gap_sec:>7.1f}s | {'YES' if is_feasible else 'NO':<10} | {status_reason}"
        )

    print("-" * 85)
    print(f"Overall Plan Feasibility: {'FEASIBLE' if all_feasible else 'INFEASIBLE'}")
    print(f"Total Buses: {len(bus_routes)}, Total Distance: {total_km:.1f} km")
    print("=" * 85)

    return {
        "shift": shift,
        "all_feasible": all_feasible,
        "total_buses": len(bus_routes),
        "total_km": round(total_km, 1),
        "routes": route_evaluations,
    }


# The Phase 0 "before" Shift A plan extracted directly from diagnostic_before.txt
PHASE0_SHIFT_A_PLAN = {
    "Bus 1": [
        "Shastri Showk",
        "Bharat Mata Chowk",
        "Lakshmi Chowk Mosi",
        "Khed, Rajgurunagar",
        "Ambethan Chowk Chakan",
        "Kharabwadi Chakan",
    ],
    "Bus 2": [
        "D Y Patil Ambi Talegaon",
        "Indrayani English School Yashwant Nagar",
        "Jijamata Chauk Talegaonndabhade",
        "Chourai Parandwadi Road",
        "Vedant Khanaval Chikhali Dehuroad",
        "Dehugaon",
    ],
    "Bus 3": [
        "Lonavala Malvali",
        "Pimpoli Kamshet",
        "Navlakh Umbare",
        "Indori Talegaon",
    ],
    "Bus 4": [
        "Vadgaon",
        "Keshavnagar Vadgaon",
        "Vadgaon Matroshri Hospital",
        "Imperial Hospital",
        "Bharat Petrol Pump Talegaon Station",
        "Varale Phata, Talegaon",
        "Indrayani Collage Talegaon",
        "Eco City Varale Talegaon",
        "Ambi Chowk",
        "Malwadi, Talegaon",
    ],
    "Bus 5": [
        "New Sangvi Krishna Chowk",
        "Bijalinagar Bridge, Chinchwad",
        "Walhekarwadi",
        "Chintamani Chauk Bijalinagar",
        "Punawale Katewasti",
        "Glamoria Front Of Runal Gateway Ravet",
        "Chandrabhaga Corner Ravet",
        "Adharsh Nagar Ravet",
    ],
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate a bus routing plan on current metrics.")
    parser.add_argument("--plan-json", type=Path, default=None, help="Path to plan JSON file")
    parser.add_argument("--roster", type=Path, default=None, help="Path to Excel roster")
    parser.add_argument("--shift", type=str, default="A", help="Shift letter (A, B, or C)")
    args = parser.parse_args()

    roster_file = args.roster
    if not roster_file:
        fixtures_dir = PROJECT_ROOT / "tests" / "fixtures"
        rosters = list(fixtures_dir.glob("*.xlsx"))
        if rosters:
            roster_file = rosters[0]
        else:
            roster_file = PROJECT_ROOT / "data" / "input" / "MAIN_SHEET_TWO_RAW_DATA.xlsx"

    if args.plan_json and args.plan_json.exists():
        with open(args.plan_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        plan_routes = data.get("routes", data)
    else:
        print("Running evaluation on Phase 0 Shift A 'Before' Plan (from diagnostic_before.txt)...")
        plan_routes = PHASE0_SHIFT_A_PLAN

    res = evaluate_stop_sequences(plan_routes, roster_file, shift=args.shift)

