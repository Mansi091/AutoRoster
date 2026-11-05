"""Universal acceptance and regression test suite for run_route_planner.py.

RULE: NO literal limits, roster-specific data, bus counts, shift names or coordinates
in code or tests. Every number comes from config (backend/app/core/config.py reading
.env) or is derived dynamically from the input roster file. Tests work on ANY roster.
"""

import sys
import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import config
from run_route_planner import (
    load_all_verified_stops,
    solve_shift_routes,
    solver_budget,
    _primary_shift,
    compute_plan_signature,
    get_plan_history_path,
    BUS_CAPACITY,
    ARRIVAL_BUFFER_MIN,
    PICKUP_WINDOW_HARD,
    MAX_RIDE_TIME_SEC,
    MAX_RIDE_TIME_MIN,
    ROUTE_TIME_TOLERANCE_SEC,
    KM_REGRESSION_TOLERANCE_PCT,
    FEASIBILITY_SOURCE,
)


def get_facility_from_config() -> Dict[str, Any]:
    """Dynamically get confirmed facility coordinates from application configuration."""
    lat = getattr(config.FACILITY, "lat", None)
    lon = getattr(config.FACILITY, "lon", None)
    name = getattr(config.FACILITY, "name", "Drop-off Facility")
    if lat is None or lon is None:
        raise ValueError("Drop-off facility coordinates must be provided in config / .env (FACILITY_LAT, FACILITY_LON).")
    return {"name": name, "lat": float(lat), "lon": float(lon)}


def discover_fixtures() -> List[Path]:
    """Find all Excel roster files in tests/fixtures directory."""
    fixtures_dir = PROJECT_ROOT / "tests" / "fixtures"
    roster_files = list(fixtures_dir.glob("*.xlsx")) + list(fixtures_dir.glob("*.xls"))
    if not roster_files:
        raise FileNotFoundError(f"No roster fixtures found in {fixtures_dir}")
    return sorted(roster_files)


def parse_roster_file(roster_path: Path) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Parse any supervisor roster file, dynamically extracting all employees and shift identifiers."""
    df = pd.read_excel(roster_path, header=1).dropna(how="all").reset_index(drop=True)

    # Dynamically find column names
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

    return all_employees, sorted(list(found_shifts))


def assert_solver_vs_validator_agreement(res: Dict[str, Any], tolerance_sec: float):
    """Section A test: For every route, |solver - validator| <= ROUTE_TIME_TOLERANCE_SEC.
    Never widen the tolerance inside the test; if it fails, print the failing legs.
    """
    routes = res.get("routes", [])
    assert len(routes) > 0, f"No routes returned for shift {res.get('shift')}"

    for r in routes:
        bus_no = r["route_no"]
        solver_sec = float(r["solver_duration_sec"])
        validator_sec = float(r["route_duration_sec"])
        gap = abs(solver_sec - validator_sec)

        print(f"  [Time Check] Bus {bus_no}: solver={solver_sec:.1f}s, validator={validator_sec:.1f}s, gap={gap:.1f}s (tolerance={tolerance_sec:.1f}s)")

        if gap > tolerance_sec:
            # Print failing legs
            print(f"\n  [FAILING LEGS] Bus {bus_no} exceeded tolerance {tolerance_sec:.1f}s with gap {gap:.1f}s:")
            stops = r.get("stops", [])
            for leg_idx in range(len(stops)):
                from_stop = stops[leg_idx]["stop_name"]
                to_stop = stops[leg_idx + 1]["stop_name"] if leg_idx + 1 < len(stops) else "Facility"
                print(f"    Leg {leg_idx + 1}: {from_stop} -> {to_stop}")

        assert gap <= tolerance_sec, (
            f"Bus {bus_no} time gap {gap:.1f}s exceeded ROUTE_TIME_TOLERANCE_SEC ({tolerance_sec:.1f}s)!"
        )


def assert_universal_invariants(res: Dict[str, Any], shift_employees: List[Dict[str, Any]]):
    """Universal invariants for any roster:
    - capacity <= BUS_CAPACITY
    - ride <= derived hard limit (MAX_RIDE_TIME_SEC)
    - first pickup >= shift start - PICKUP_WINDOW_HARD
    - every employee on a bus or in outliers
    """
    routes = res.get("routes", [])
    outliers = res.get("outliers", [])
    shift_name = res.get("shift", "")

    # 1. Employee accounting
    assigned_passengers = sum(r["total_passengers"] for r in routes)
    outlier_count = len(outliers)
    total_roster_emps = len(shift_employees)
    assert assigned_passengers + outlier_count == total_roster_emps, (
        f"Shift {shift_name} accounting mismatch: {assigned_passengers} assigned + {outlier_count} outliers != {total_roster_emps} total"
    )

    # 2. Per-route invariants
    shift_start_min = config.SHIFT_START_TIMES[shift_name]

    for r in routes:
        bus_no = r["route_no"]
        # Capacity constraint
        assert r["total_passengers"] <= BUS_CAPACITY, (
            f"Bus {bus_no} exceeded BUS_CAPACITY ({BUS_CAPACITY}): had {r['total_passengers']} passengers"
        )
        # Ride time hard limit
        assert r["route_duration_sec"] <= MAX_RIDE_TIME_SEC + 1.0, (
            f"Bus {bus_no} duration {r['route_duration_sec']:.1f}s exceeded MAX_RIDE_TIME_SEC ({MAX_RIDE_TIME_SEC}s)"
        )
        # First pickup window hard limit
        first_pickup_min = r["stops"][0]["pickup_minute_of_day"]
        pickup_window = shift_start_min - first_pickup_min
        assert pickup_window <= PICKUP_WINDOW_HARD + 0.5, (
            f"Bus {bus_no} first pickup {pickup_window:.1f}m before shift start exceeded PICKUP_WINDOW_HARD ({PICKUP_WINDOW_HARD}m)"
        )


def assert_no_regression(res: Dict[str, Any], stored_plan: Optional[Dict[str, Any]]):
    """Section C test: The new plan never has more buses than the best feasible stored plan,
    and at equal buses km <= stored km * (1 + KM_REGRESSION_TOLERANCE_PCT / 100).
    """
    if not stored_plan:
        print("  [Regression Check] No prior stored plan for this signature yet (baseline created).")
        return

    stored_buses = stored_plan["buses"]
    stored_km = float(stored_plan["total_km"])
    new_buses = res["buses_used"]
    new_km = float(res["total_distance_km"])

    print(f"  [Regression Check] Stored plan: {stored_buses} buses, {stored_km:.1f} km | New plan: {new_buses} buses, {new_km:.1f} km")

    # Bus count must not increase
    assert new_buses <= stored_buses, (
        f"Regression detected: New plan required {new_buses} buses, exceeding stored plan of {stored_buses} buses!"
    )

    # At equal bus count, km must not exceed tolerance
    if new_buses == stored_buses:
        max_allowed_km = stored_km * (1.0 + (KM_REGRESSION_TOLERANCE_PCT / 100.0))
        assert new_km <= max_allowed_km + 0.05, (
            f"Regression detected: New km {new_km:.1f} exceeded allowed km {max_allowed_km:.1f} "
            f"({KM_REGRESSION_TOLERANCE_PCT:g}% tolerance over stored {stored_km:.1f} km)"
        )


def run_all_tests():
    print("=" * 80)
    print("AUTOROSTER UNIVERSAL ACCEPTANCE & REGRESSION SUITE")
    print(f"Limits: BUS_CAPACITY={BUS_CAPACITY}, MAX_RIDE_TIME_SEC={MAX_RIDE_TIME_SEC}s ({MAX_RIDE_TIME_MIN:g}m)")
    print(f"Tolerance: ROUTE_TIME_TOLERANCE_SEC={ROUTE_TIME_TOLERANCE_SEC}s, KM_REGRESSION_TOLERANCE_PCT={KM_REGRESSION_TOLERANCE_PCT}%")
    print(f"Feasibility Source: {FEASIBILITY_SOURCE}")
    print("=" * 80)

    facility = get_facility_from_config()
    print(f"Drop-off Facility: {facility['name']} ({facility['lat']:.6f}, {facility['lon']:.6f})")

    stop_lookup = load_all_verified_stops()
    fixtures = discover_fixtures()

    total_shifts_tested = 0

    for fixture_path in fixtures:
        print(f"\nTesting Roster Fixture: {fixture_path.name}")
        all_employees, shifts_in_file = parse_roster_file(fixture_path)
        print(f"Found {len(all_employees)} total employees across shifts: {shifts_in_file}")

        for shift in shifts_in_file:
            shift_emps = [e for e in all_employees if e["shift"] == shift]
            if not shift_emps:
                continue

            print(f"\n--- Testing Shift {shift} ({len(shift_emps)} employees) ---")
            total_shifts_tested += 1

            # Check if there is an existing stored plan before this solve
            # We will read it from history if available
            res = solve_shift_routes(shift, shift_emps, stop_lookup, facility, search_budget=solver_budget("job"))

            # Test 1: Solver vs Validator Time Agreement
            assert_solver_vs_validator_agreement(res, ROUTE_TIME_TOLERANCE_SEC)
            print("  Test 1 (Solver vs Validator Agreement): PASSED")

            # Test 2: Universal Invariants
            assert_universal_invariants(res, shift_emps)
            print("  Test 2 (Universal Invariants): PASSED")

            # Test 3: No-Regression Rule
            # In solve_shift_routes, the plan was saved to plan history.
            # Running solve_shift_routes again with the exact same inputs should load the stored plan,
            # warm-start from it, and guarantee NO regression in buses or km!
            sig = res.get("plan_signature")
            plan_path = get_plan_history_path(shift, sig) if sig else None
            stored_data = None
            if plan_path and plan_path.exists():
                try:
                    with open(plan_path, "r", encoding="utf-8") as f:
                        stored_data = json.load(f)
                except Exception:
                    stored_data = None

            assert_no_regression(res, stored_data)
            print("  Test 3 (No Regression Rule): PASSED")

    print("\n" + "=" * 80)
    print(f"ALL TESTS PASSED! Verified {total_shifts_tested} shifts across all fixtures.")
    print("=" * 80)


if __name__ == "__main__":
    run_all_tests()
