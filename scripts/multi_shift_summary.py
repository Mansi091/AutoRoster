"""Run multi-shift planning across all shifts present in the roster and output the Section D summary table.

RULE: No literal limits, roster-specific data, bus counts, shift names or coordinates in code or tests.
All configuration is read dynamically from config / .env.
"""

import sys
import argparse
from pathlib import Path
from typing import Dict, Any, Optional
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import config
from run_route_planner import (
    load_all_verified_stops,
    solve_shift_routes,
    _primary_shift,
    BUS_CAPACITY,
    MAX_RIDE_TIME_SEC,
    MAX_RIDE_TIME_MIN,
    ROUTE_TIME_TOLERANCE_SEC,
    FEASIBILITY_SOURCE,
    PLAN_HISTORY_MODE,
)


def get_facility() -> Dict[str, Any]:
    lat = getattr(config.FACILITY, "lat", None)
    lon = getattr(config.FACILITY, "lon", None)
    name = getattr(config.FACILITY, "name", "Drop-off Facility")
    if lat is None or lon is None:
        raise ValueError("Drop-off facility coordinates must be provided in config (FACILITY_LAT, FACILITY_LON).")
    return {"name": name, "lat": float(lat), "lon": float(lon)}


def run_all_shifts_report(roster_path: Path, history_mode: str = "ignore"):
    print("=" * 85)
    print("AUTOROSTER ALL-SHIFTS EXECUTION & COMPREHENSIVE METRICS REPORT")
    print(f"Roster File: {roster_path.name}")
    facility = get_facility()
    print(f"Drop-off Facility: {facility['name']} ({facility['lat']:.6f}, {facility['lon']:.6f})")
    print(f"Parameters: BUS_CAPACITY={BUS_CAPACITY}, MAX_RIDE_TIME={MAX_RIDE_TIME_MIN:g}m ({MAX_RIDE_TIME_SEC}s), FEASIBILITY_SOURCE={FEASIBILITY_SOURCE}")
    print(f"PLAN_HISTORY_MODE: {history_mode}")
    print("=" * 85)

    stop_lookup = load_all_verified_stops()
    df = pd.read_excel(roster_path, header=1).dropna(how="all").reset_index(drop=True)

    col_map = {str(c).strip().lower(): c for c in df.columns}
    grade_col = col_map.get("grade") or col_map.get("employee type") or col_map.get("type")
    name_col = col_map.get("name") or col_map.get("employee name") or col_map.get("emp name")
    shift_col = col_map.get("shift")
    stop_col = col_map.get("bus stop") or col_map.get("pickup point") or col_map.get("stop")
    mob_col = col_map.get("mob. no.") or col_map.get("mobile") or col_map.get("mobile no.")

    all_employees = []
    found_shifts = set()

    for idx, r in df.iterrows():
        raw_shift = str(r.get(shift_col, "A")).strip() if shift_col else "A"
        clean_shift = _primary_shift(raw_shift)
        found_shifts.add(clean_shift)

        all_employees.append({
            "sr_no": idx + 1,
            "emp_type": str(r.get(grade_col, "DET")).strip() if grade_col else "DET",
            "emp_name": str(r.get(name_col, f"Emp_{idx+1}")).strip() if name_col else f"Emp_{idx+1}",
            "shift": clean_shift,
            "raw_stop": str(r.get(stop_col, "")).strip() if stop_col else "",
            "mobile": str(r.get(mob_col, "")).replace(".0", "").strip() if mob_col else "",
        })

    shift_list = sorted(list(found_shifts))
    print(f"Discovered {len(all_employees)} employees across {len(shift_list)} shifts: {shift_list}\n")

    summary_rows = []

    for shift in shift_list:
        shift_emps = [e for e in all_employees if e["shift"] == shift]
        if not shift_emps:
            continue

        print(f"\n{'#' * 30} SOLVING SHIFT {shift} ({len(shift_emps)} employees, history_mode={history_mode}) {'#' * 30}")
        res = solve_shift_routes(shift, shift_emps, stop_lookup, facility, plan_history_mode=history_mode)

        routes = res.get("routes", [])
        num_buses = res.get("buses_used", 0)
        total_pax = res.get("assigned_passengers", 0)
        avg_pax = round(total_pax / num_buses, 1) if num_buses > 0 else 0.0
        total_km = res.get("total_distance_km", 0.0)
        max_ride_min = res.get("longest_ride_time_min", 0.0)

        # Max solver-vs-route gap
        max_gap_sec = 0.0
        for r in routes:
            gap = r.get("duration_gap_sec", abs(r["solver_duration_sec"] - r["route_duration_sec"]))
            if gap > max_gap_sec:
                max_gap_sec = gap

        summary_rows.append({
            "shift": shift,
            "employees": len(shift_emps),
            "buses": num_buses,
            "avg_pax": avg_pax,
            "total_km": total_km,
            "max_ride_min": max_ride_min,
            "max_gap_sec": round(max_gap_sec, 1),
        })

    print("\n" + "=" * 85)
    print("FINAL MULTI-SHIFT EXECUTION SUMMARY TABLE")
    print("=" * 85)
    print(f"{'Shift':<8} | {'Employees':<10} | {'Buses':<6} | {'Avg Pax':<8} | {'Total km':<10} | {'Max Ride (m)':<13} | {'Max Gap (s)':<12}")
    print("-" * 85)
    for row in summary_rows:
        print(
            f"{row['shift']:<8} | {row['employees']:<10} | {row['buses']:<6} | {row['avg_pax']:<8.1f} | "
            f"{row['total_km']:<10.1f} | {row['max_ride_min']:<13.1f} | {row['max_gap_sec']:<12.1f}"
        )
    print("=" * 85)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-shift report across all shifts.")
    parser.add_argument("--history-mode", choices=["use", "ignore", "rebuild"], default="ignore", help="History mode")
    parser.add_argument("--roster", type=Path, default=None, help="Roster Excel file")
    args = parser.parse_args()

    roster_file = args.roster
    if not roster_file:
        fixtures_dir = PROJECT_ROOT / "tests" / "fixtures"
        fixture_files = list(fixtures_dir.glob("*.xlsx")) + list(fixtures_dir.glob("*.xls"))
        if fixture_files:
            roster_file = fixture_files[0]
        else:
            print(f"No fixtures found in {fixtures_dir}")
            sys.exit(1)

    run_all_shifts_report(roster_file, history_mode=args.history_mode)
