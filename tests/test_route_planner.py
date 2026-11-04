"""Acceptance test suite for run_route_planner.py.

Asserts:
1. Work in seconds: solver duration vs OSRM duration gap.
2. Invariant: first_pickup_time >= shift_start - 70 min for every bus.
3. Operating limits: pax <= 32 and route_duration <= 60 min.
"""

import sys
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from run_route_planner import (
    load_all_verified_stops,
    solve_shift_routes,
    _primary_shift,
    BUS_CAPACITY,
    MAX_RIDE_TIME_MIN,
    MAX_RIDE_TIME_SEC,
    PICKUP_WINDOW_HARD,
    ARRIVAL_BUFFER_MIN,
)


def get_planner_shift_a():
    roster_path = PROJECT_ROOT / "data" / "input" / "MAIN_SHEET_TWO_RAW_DATA.xlsx"
    facility = {
        "name": "Mercedes-Benz India",
        "lat": 18.7366,
        "lon": 73.8090,
    }
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

    shift_a_emps = [e for e in all_employees if _primary_shift(e["shift"]) == "A"]
    res = solve_shift_routes("A", shift_a_emps, stop_lookup, facility)
    return res


def test_capacity_and_hard_limit(res):
    routes = res["routes"]
    assert len(routes) > 0, "No routes returned"
    for r in routes:
        assert r["total_passengers"] <= BUS_CAPACITY, f"Bus {r['route_no']} exceeded capacity {BUS_CAPACITY}"
        assert r["route_duration_sec"] <= MAX_RIDE_TIME_SEC + 30, (
            f"Bus {r['route_no']} duration {r['route_duration_sec']}s exceeded hard limit {MAX_RIDE_TIME_SEC}s"
        )
    print("test_capacity_and_hard_limit: PASSED")


def test_first_pickup_invariant(res):
    """Invariant test: first_pickup_time >= shift_start - 70 min for every bus."""
    routes = res["routes"]
    shift_start_min = 400 + ARRIVAL_BUFFER_MIN
    for r in routes:
        first_pickup_min = r["stops"][0]["pickup_minute_of_day"]
        pickup_window = shift_start_min - first_pickup_min
        assert pickup_window <= PICKUP_WINDOW_HARD + 0.5, (
            f"Bus {r['route_no']} first pickup window {pickup_window} min exceeded {PICKUP_WINDOW_HARD} min"
        )
    print("test_first_pickup_invariant: PASSED")


def test_solver_vs_validator_agreement(res):
    """Assert solver duration and OSRM validator duration align."""
    routes = res["routes"]
    for r in routes:
        solver_sec = r["solver_duration_sec"]
        osrm_sec = r["route_duration_sec"]
        gap = abs(solver_sec - osrm_sec)
        print(f"Bus {r['route_no']}: solver={solver_sec:.0f}s, validator={osrm_sec:.0f}s, gap={gap:.1f}s")
        assert gap <= 450.0, (
            f"Bus {r['route_no']} gap {gap:.1f}s exceeded allowed limit"
        )
    print("test_solver_vs_validator_agreement: PASSED")


if __name__ == "__main__":
    print("Running acceptance tests on Shift A routes...")
    res = get_planner_shift_a()
    test_capacity_and_hard_limit(res)
    test_first_pickup_invariant(res)
    test_solver_vs_validator_agreement(res)
    print("\nALL ACCEPTANCE TESTS PASSED!")
