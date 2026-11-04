"""Route Planner Diagnostic Tool.

Evaluates solve_shift_routes output:
- Per bus: stops, pax, solver_duration (rounded matrix + ceil dwell), osrm_duration (real leg seconds + real dwell), difference.
- Per stop: cheapest insertion cost into every other bus in solver minutes and real OSRM minutes, and feasibility.
"""

import sys
import math
import argparse
from pathlib import Path
from typing import Dict, List, Any, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import requests
import pandas as pd

from run_route_planner import (
    load_all_verified_stops,
    _master_match,
    _stop_key,
    _primary_shift,
    solve_shift_routes,
    fetch_osrm_route_geometry,
    config,
    BUS_CAPACITY,
    MAX_RIDE_TIME_MIN,
    TARGET_RIDE_TIME_MIN,
    DWELL_TIME_MIN,
    BOARDING_SECONDS_PER_PASSENGER,
)
from backend.app.etl.transform.coordinate_resolver import resolve_roster_pickup_coordinates
from backend.app.etl.transform.candidate_scoring import evaluate_coordinate_candidate


def get_raw_osrm_table(coords: List[Tuple[float, float]]) -> Tuple[List[List[float]], List[List[float]]]:
    """Fetch raw unrounded durations (seconds) and distances (meters) from OSRM Table API."""
    n = len(coords)
    if n <= 1:
        return [[0.0]], [[0.0]]
    coords_str = ";".join([f"{lon:.6f},{lat:.6f}" for lat, lon in coords])
    url = f"{config.OSRM_TABLE_URL.rstrip('/')}/{coords_str}?annotations=distance,duration"
    res = requests.get(url, timeout=15, headers={"User-Agent": "AutoRoster-Diagnostic/1.0"})
    res.raise_for_status()
    data = res.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM Table API returned: {data.get('code')}")
    return data["durations"], data["distances"]


def run_diagnostic(
    roster_path: Path,
    facility_lat: float,
    facility_lon: float,
    facility_name: str,
    shift: str,
):
    facility = {
        "name": facility_name,
        "lat": facility_lat,
        "lon": facility_lon,
    }

    print("=" * 80)
    print("ROUTE PLANNER DIAGNOSTIC RUN")
    print(f"Roster: {roster_path}")
    print(f"Facility: {facility['name']} ({facility['lat']:.4f}, {facility['lon']:.4f})")
    print(f"Shift: {shift}")
    print(f"Limits: BUS_CAPACITY={BUS_CAPACITY}, TARGET_RIDE={TARGET_RIDE_TIME_MIN}m, MAX_RIDE={MAX_RIDE_TIME_MIN}m")
    print("=" * 80)

    # 1. Load stop lookup and resolve coordinates
    stop_lookup = load_all_verified_stops()
    df = pd.read_excel(roster_path, header=1).dropna(how="all").reset_index(drop=True)

    raw_names = list(dict.fromkeys(
        str(value).strip()
        for value in df.get("Bus Stop", pd.Series(dtype=str)).tolist()
        if str(value).strip() and str(value).strip().lower() != "nan"
    ))
    unmatched = []
    for name in raw_names:
        m = _master_match(name, stop_lookup)
        if m:
            st, _ = evaluate_coordinate_candidate(m["lat"], m["lon"], facility=facility)
            if st == "VERIFIED":
                stop_lookup[_stop_key(name)] = m
                continue
        unmatched.append(name)

    res_details = {}
    if unmatched:
        print(f"[Geocoding] Resolving {len(unmatched)} unmatched stop labels...")
        res_details = resolve_roster_pickup_coordinates(unmatched, facility)
        for raw_name, resolution in res_details.items():
            if resolution.get("location_status") == "VERIFIED":
                item = {
                    "name": resolution.get("canonical_name") or raw_name,
                    "lat": float(resolution["latitude"]),
                    "lon": float(resolution["longitude"]),
                }
                stop_lookup[_stop_key(raw_name)] = item
                stop_lookup[_stop_key(str(item["name"]))] = item

    # 2. Extract employees for shift
    all_employees = []
    for idx, r in df.iterrows():
        all_employees.append({
            "sr_no": idx + 1,
            "emp_type": str(r.get("GRADE", "DET")).strip(),
            "emp_name": str(r.get("Name", f"Emp_{idx+1}")).strip(),
            "shift": str(r.get("Shift", "A")).strip(),
            "raw_stop": str(r.get("Bus Stop", "")).strip(),
            "mobile": str(r.get("Mob. No.", "")).replace(".0", "").strip(),
        })

    shift_emps = [e for e in all_employees if _primary_shift(e["shift"]) == shift]
    print(f"Total roster employees: {len(all_employees)}, Shift {shift} employees: {len(shift_emps)}")

    # 3. Run solve_shift_routes
    solution = solve_shift_routes(shift, shift_emps, stop_lookup, facility, res_details)

    routes = solution.get("routes", [])
    outliers = solution.get("outliers", [])
    print(f"\nSolver result: {len(routes)} buses used, {solution.get('assigned_passengers', 0)} pax assigned, {len(outliers)} outliers")

    if not routes:
        print("No routes generated!")
        return

    # 4. Collect all unique locations across all routes + facility for precise matrix lookups
    all_stops: List[Dict[str, Any]] = []
    for bus_idx, r in enumerate(routes):
        for s in r["stops"]:
            all_stops.append({
                **s,
                "bus_no": r["route_no"],
                "bus_idx": bus_idx,
            })

    unique_coords: List[Tuple[float, float]] = []
    coord_to_idx: Dict[Tuple[float, float], int] = {}
    for s in all_stops:
        pt = (round(s["lat"], 6), round(s["lon"], 6))
        if pt not in coord_to_idx:
            coord_to_idx[pt] = len(unique_coords)
            unique_coords.append(pt)

    plant_pt = (round(facility["lat"], 6), round(facility["lon"], 6))
    if plant_pt not in coord_to_idx:
        coord_to_idx[plant_pt] = len(unique_coords)
        unique_coords.append(plant_pt)
    plant_idx = coord_to_idx[plant_pt]

    print(f"[OSRM] Fetching raw road table for {len(unique_coords)} unique locations...")
    raw_sec_matrix, raw_dist_matrix = get_raw_osrm_table(unique_coords)

    # Dwell helpers
    def solver_dwell_min(demand: int) -> int:
        """Dwell in minutes as solver sees it: ceil(DWELL_TIME_MIN + boarding_sec * demand / 60)."""
        val = DWELL_TIME_MIN + (BOARDING_SECONDS_PER_PASSENGER * demand / 60.0)
        return int(math.ceil(val))

    def real_dwell_sec(demand: int) -> float:
        """Real dwell in seconds: 90s + boarding_sec * demand."""
        return 90.0 + (BOARDING_SECONDS_PER_PASSENGER * demand)

    def solver_travel_min(u_idx: int, v_idx: int) -> int:
        """Solver road time in minutes: max(2, ceil(sec / 60))."""
        sec = raw_sec_matrix[u_idx][v_idx]
        if sec is None:
            return int(MAX_RIDE_TIME_MIN) + 1
        return max(2, int(math.ceil(sec / 60.0)))

    def real_travel_sec(u_idx: int, v_idx: int) -> float:
        """Real OSRM road time in seconds."""
        sec = raw_sec_matrix[u_idx][v_idx]
        return float(sec) if sec is not None else 1_000_000.0

    # Calculate route durations
    def route_solver_duration_min(stop_items: List[Dict[str, Any]]) -> Tuple[int, int, int]:
        """Returns (total_solver_min, travel_min, dwell_min)."""
        if not stop_items:
            return 0, 0, 0
        travel = 0
        indices = [coord_to_idx[(round(s["lat"], 6), round(s["lon"], 6))] for s in stop_items]
        for a, b in zip(indices, indices[1:]):
            travel += solver_travel_min(a, b)
        travel += solver_travel_min(indices[-1], plant_idx)
        dwell = sum(solver_dwell_min(s["demand"]) for s in stop_items)
        return travel + dwell, travel, dwell

    def route_osrm_duration_min(stop_items: List[Dict[str, Any]]) -> Tuple[float, float, float]:
        """Returns (total_osrm_min, travel_min, dwell_min) using real seconds."""
        if not stop_items:
            return 0.0, 0.0, 0.0
        travel_sec = 0.0
        indices = [coord_to_idx[(round(s["lat"], 6), round(s["lon"], 6))] for s in stop_items]
        for a, b in zip(indices, indices[1:]):
            travel_sec += real_travel_sec(a, b)
        travel_sec += real_travel_sec(indices[-1], plant_idx)
        dwell_sec = sum(real_dwell_sec(s["demand"]) for s in stop_items)
        total_sec = travel_sec + dwell_sec
        return round(total_sec / 60.0, 2), round(travel_sec / 60.0, 2), round(dwell_sec / 60.0, 2)

    # =========================================================================
    # PART 1: PER-BUS REPORT
    # =========================================================================
    print("\n" + "=" * 90)
    print(f"{'BUS ROUTE SUMMARY & DURATION GAPS':^90}")
    print("=" * 90)
    print(f"{'Bus':<6} | {'Stops':<5} | {'Pax':<4} | {'Solver (min)':<13} | {'OSRM (min)':<11} | {'Diff (min)':<10} | {'Status':<14} | Corridor")
    print("-" * 90)

    bus_stops_map: Dict[int, List[Dict[str, Any]]] = {}

    for r in routes:
        b_no = r["route_no"]
        b_stops = r["stops"]
        bus_stops_map[b_no] = b_stops
        pax = r["total_passengers"]
        
        # Solver view
        s_tot, s_trav, s_dw = route_solver_duration_min(b_stops)
        # Real OSRM view
        o_tot, o_trav, o_dw = route_osrm_duration_min(b_stops)
        diff = round(o_tot - s_tot, 2)
        diff_str = f"{diff:+.2f}"

        status = "WITHIN_LIMIT" if o_tot <= MAX_RIDE_TIME_MIN else "EXCEEDS_CAP"
        corridor = r.get("corridor_name", "Route")
        print(f"Bus {b_no:<2} | {len(b_stops):<5} | {pax:<4} | {s_tot:>4} (t:{s_trav:>2},d:{s_dw:>2}) | {o_tot:>5.1f} (t:{o_trav:>4.1f}) | {diff_str:>9} | {status:<14} | {corridor}")

    print("-" * 90)

    # =========================================================================
    # PART 2: INSERTION COST OF EVERY STOP INTO EVERY OTHER BUS
    # =========================================================================
    print("\n" + "=" * 90)
    print(f"{'CHEAPEST INSERTION ANALYSIS (EVERY STOP INTO EVERY OTHER BUS)':^90}")
    print("=" * 90)

    # For every bus and every stop
    for current_bus_no in sorted(bus_stops_map.keys()):
        current_stops = bus_stops_map[current_bus_no]
        other_bus_numbers = [b for b in sorted(bus_stops_map.keys()) if b != current_bus_no]

        print(f"\n>>> SOURCE BUS {current_bus_no} ({len(current_stops)} stops, {sum(s['demand'] for s in current_stops)} pax) <<<")

        for stop_idx, stop in enumerate(current_stops):
            stop_name = stop["stop_name"]
            stop_demand = stop["demand"]

            print(f"\n  Stop [{stop_idx+1}/{len(current_stops)}]: \"{stop_name}\" (Pax: {stop_demand})")

            for target_bus_no in other_bus_numbers:
                target_stops = bus_stops_map[target_bus_no]
                target_pax = sum(s["demand"] for s in target_stops)
                new_pax = target_pax + stop_demand

                # Current base durations for target bus
                curr_s_tot, _, _ = route_solver_duration_min(target_stops)
                curr_o_tot, _, _ = route_osrm_duration_min(target_stops)

                # Evaluate all insertion positions
                best_solver = None
                best_osrm = None

                for pos in range(len(target_stops) + 1):
                    cand_stops = target_stops[:pos] + [stop] + target_stops[pos:]

                    # Solver evaluation
                    c_s_tot, _, _ = route_solver_duration_min(cand_stops)
                    s_delta = c_s_tot - curr_s_tot
                    s_feasible = (new_pax <= BUS_CAPACITY) and (c_s_tot <= MAX_RIDE_TIME_MIN)

                    if best_solver is None or s_delta < best_solver["delta"]:
                        best_solver = {
                            "pos": pos,
                            "delta": s_delta,
                            "new_tot": c_s_tot,
                            "feasible": s_feasible,
                        }

                    # OSRM evaluation
                    c_o_tot, _, _ = route_osrm_duration_min(cand_stops)
                    o_delta = round(c_o_tot - curr_o_tot, 2)
                    o_feasible = (new_pax <= BUS_CAPACITY) and (c_o_tot <= MAX_RIDE_TIME_MIN)

                    if best_osrm is None or o_delta < best_osrm["delta"]:
                        best_osrm = {
                            "pos": pos,
                            "delta": o_delta,
                            "new_tot": c_o_tot,
                            "feasible": o_feasible,
                        }

                s_feas_str = "FEASIBLE" if best_solver["feasible"] else "INFEASIBLE"
                o_feas_str = "FEASIBLE" if best_osrm["feasible"] else "INFEASIBLE"

                print(
                    f"    -> Bus {target_bus_no} (curr: {len(target_stops)} stops, {target_pax} pax, S:{curr_s_tot}m, O:{curr_o_tot:.1f}m):"
                )
                print(
                    f"       * SOLVER best @ pos {best_solver['pos']}: delta = {best_solver['delta']:+d} min "
                    f"(new: {best_solver['new_tot']} min, pax: {new_pax}/32) => [{s_feas_str}]"
                )
                print(
                    f"       * OSRM   best @ pos {best_osrm['pos']}: delta = {best_osrm['delta']:+.2f} min "
                    f"(new: {best_osrm['new_tot']:.1f} min, pax: {new_pax}/32) => [{o_feas_str}]"
                )

    print("\n" + "=" * 90)
    print("DIAGNOSTIC RUN COMPLETE")
    print("=" * 90)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AutoRoster Route Planner Diagnostic")
    parser.add_argument("--roster", type=Path, default=PROJECT_ROOT / "data" / "input" / "MAIN_SHEET_TWO_RAW_DATA.xlsx")
    parser.add_argument("--facility-lat", type=float, default=18.7366)
    parser.add_argument("--facility-lon", type=float, default=73.8090)
    parser.add_argument("--facility-name", type=str, default="Mercedes-Benz India")
    parser.add_argument("--shift", type=str, default="A")
    args = parser.parse_args()

    run_diagnostic(
        roster_path=args.roster,
        facility_lat=args.facility_lat,
        facility_lon=args.facility_lon,
        facility_name=args.facility_name,
        shift=args.shift,
    )

