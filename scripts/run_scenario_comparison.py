import os
import sys
from pathlib import Path

PROJECT_ROOT = Path("D:/bus_routing_project")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from backend.app.core.config import config, load_scenarios
from backend.app.etl.transform.coordinate_resolver import resolve_roster_pickup_coordinates
from run_route_planner import evaluate_all_scenarios, load_all_verified_stops, _filter_roster_to_shift, _primary_shift, solve_shift_routes

def main():
    roster_path = PROJECT_ROOT / "tests" / "fixtures" / "sample_roster.xlsx"
    facility = {
        "name": getattr(config, "FACILITY_NAME", "Mercedes-Benz India"),
        "lat": float(getattr(config, "FACILITY_LAT", 18.7366)),
        "lon": float(getattr(config, "FACILITY_LON", 73.8090)),
    }

    print("=" * 115)
    print("AUTOROSTER SCENARIO COMPARISON & OPERATIONAL SENSITIVITY EVALUATION")
    print(f"Roster File: {roster_path.name}")
    print(f"Facility: {facility['name']} ({facility['lat']:.6f}, {facility['lon']:.6f})")
    print(f"ROUTE_NEAR_LIMIT_SEC: {config.ROUTE_NEAR_LIMIT_SEC:.0f}s")
    print("=" * 115)

    stop_lookup = load_all_verified_stops()
    df = pd.read_excel(roster_path, header=1).dropna(how="all").reset_index(drop=True)
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

    # Resolve coordinates
    raw_names = list(dict.fromkeys(
        str(value).strip()
        for value in df.get("Bus Stop", pd.Series(dtype=str)).tolist()
        if str(value).strip() and str(value).strip().lower() != "nan"
    ))
    resolution_details = resolve_roster_pickup_coordinates(raw_names, facility)
    for raw_name, resolution in resolution_details.items():
        if resolution.get("location_status") == "VERIFIED":
            stop_lookup[raw_name.lower().replace(" ", "")] = {
                "name": resolution.get("canonical_name") or raw_name,
                "lat": float(resolution["latitude"]),
                "lon": float(resolution["longitude"]),
            }

    # Evaluate scenarios cold across all shifts
    scenarios = load_scenarios()
    report = evaluate_all_scenarios(
        all_employees,
        stop_lookup,
        facility,
        resolution_details,
        shift_start_times=dict(config.SHIFT_START_TIMES),
        shift_filter="ALL",
        scenarios=scenarios,
    )

    table = report["comparison_table"]
    shift_keys = sorted({k for row in table for k in row["buses_by_shift"]})
    shift_cols = " | ".join(f"{k:>3}" for k in shift_keys)
    client_rule = table[0]["client_pickup_window_hard_min"] if table else 0.0
    width = 150

    print("\n" + "=" * width)
    print(f"Client rule: first pickup at most PICKUP_WINDOW_HARD={client_rule:g} min before shift start (current config)")
    print("-" * width)
    print(f"{'SCENARIO NAME':<46} | {shift_cols} | {'TOTAL':<5} | {'SAVED':<5} | {'TOTAL KM':<8} | {'MAX RIDE':<8} | {'MAX WINDOW':<10} | {'NEAR':<4} | CLIENT RULE")
    print("-" * width)
    for row in table:
        saved = row["buses_saved_vs_current"]
        saved_str = f"+{saved}" if saved > 0 else str(saved)
        per_shift = " | ".join(f"{row['buses_by_shift'].get(k, 0):>3}" for k in shift_keys)
        print(
            f"{row['scenario_name']:<46} | {per_shift} | {row['buses']:<5} | {saved_str:<5} | "
            f"{row['total_km']:<8.1f} | {row['max_ride_min']:<8.1f} | "
            f"{row['max_first_pickup_before_shift_min']:<10.1f} | {row['near_limit_buses']:<4} | {row['client_rule_status']}"
        )
    print("=" * width)

    print("\n[Near-Limit Commute Diagnostics by Scenario]")
    for sc_name, plan in report["scenario_plans"].items():
        near_limit_routes = []
        for s in plan["shifts"]:
            for r in s.get("routes", []):
                if r.get("is_near_hard_limit"):
                    near_limit_routes.append((s["shift"], r["route_no"], r["route_duration_sec"], r["hard_limit_status"]))
        if near_limit_routes:
            print(f"  * {sc_name}:")
            for s_name, r_no, r_sec, status in near_limit_routes:
                print(f"      Shift {s_name} Bus {r_no}: {r_sec:.1f}s ({status})")
        else:
            print(f"  * {sc_name}: 0 near-limit routes.")

if __name__ == "__main__":
    main()

