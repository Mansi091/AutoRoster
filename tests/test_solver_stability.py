"""Solver stability with the background-job search budget.

1. Solve the sample roster 3 times with solver_budget("job"): run 1 inline, runs 2-3
   on a process pool (as jobs do). Bus count per shift must be identical; the km
   spread across runs is printed. Every search must stop on the solution limit (not
   the time cap), otherwise results are machine-dependent.
2. Plan history guard: store a plan, then solve again with a much smaller budget
   (a "slower machine"). The later run must never use MORE buses than the stored plan.

Needs the sample roster fixture and OSRM (served from data/cache when warm).
Every limit comes from config; nothing is hardcoded.
"""

import multiprocessing
import os
import sys
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

import run_route_planner as rrp  # noqa: E402
from backend.app.core.config import config  # noqa: E402
from test_route_planner import discover_fixtures, get_facility_from_config, parse_roster_file  # noqa: E402

RUNS = 3


def _planning_input(roster_path: Path):
    employees, shifts = parse_roster_file(roster_path)
    return {
        "all_employees": employees,
        "stop_lookup": rrp.load_all_verified_stops(),
        "resolution_details": {},
        "shift_keys": sorted(shifts),
    }


def _summary(outputs):
    return {
        out["result"]["shift"]: {
            "buses": out["result"]["buses_used"],
            "km": out["result"]["total_distance_km"],
            "elapsed_sec": out["elapsed_sec"],
            "reproducible": out["result"]["optimization"]["reproducible"],
            "stats": out["result"]["optimization"]["search_stats"],
        }
        for out in outputs
    }


def _spawn_pool(size: int, env: dict = None) -> ProcessPoolExecutor:
    """Process pool like the job runner's; `env` is applied before workers spawn."""
    for key, value in (env or {}).items():
        os.environ[key] = value
    return ProcessPoolExecutor(max_workers=size, mp_context=multiprocessing.get_context("spawn"))


class SolverStability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures = discover_fixtures()
        if not fixtures:
            raise unittest.SkipTest("No roster fixtures in tests/fixtures")
        cls.roster = fixtures[0]
        cls.facility = get_facility_from_config()
        cls.planning_input = _planning_input(cls.roster)
        cls.budget = rrp.solver_budget("job")
        if cls.budget["solution_limit"] <= 0:
            raise AssertionError("SOLVER_SOLUTION_LIMIT must be > 0 for reproducible job solves")

    def test_three_runs_same_buses_per_shift(self):
        tasks = rrp.shift_solve_tasks(self.planning_input, self.facility, None, "ignore", self.budget)
        runs = [_summary(rrp.run_solve_tasks(tasks))]
        with _spawn_pool(max(2, config.JOB_SHIFT_POOL_SIZE)) as pool:
            for _ in range(RUNS - 1):
                runs.append(_summary(rrp.run_solve_tasks(tasks, pool=pool)))

        print(f"\nStability ({self.roster.name}, budget {self.budget['solution_limit']} solutions / "
              f"{self.budget['time_limit_seconds']}s cap per strategy; run 1 inline, runs 2-{RUNS} process pool)")
        print(f"{'Shift':<6}" + "".join(f"| Run {i + 1} buses / km / sec      " for i in range(RUNS)) + "| km spread")
        for shift in self.planning_input["shift_keys"]:
            cells = "".join(
                f"| {r[shift]['buses']:>2} / {r[shift]['km']:>6.1f} / {r[shift]['elapsed_sec']:>5.1f}s      " for r in runs
            )
            kms = [r[shift]["km"] for r in runs]
            print(f"{shift:<6}{cells}| {max(kms) - min(kms):.1f}")

        for shift in self.planning_input["shift_keys"]:
            for i, run in enumerate(runs):
                stopped = [s for s in run[shift]["stats"] if not s["reproducible"]]
                self.assertFalse(stopped, f"Run {i + 1} shift {shift}: search hit the time cap, not the solution limit: {stopped}")
            buses = [r[shift]["buses"] for r in runs]
            self.assertEqual(len(set(buses)), 1, f"Shift {shift} bus count differs across runs: {buses}")

    def test_slower_machine_never_uses_more_buses_than_stored_plan(self):
        slow_budget = dict(self.budget, solution_limit=max(1, self.budget["solution_limit"] // 10))
        with tempfile.TemporaryDirectory() as history_dir:
            # Workers spawned after this read PLAN_HISTORY_DIR from the environment.
            previous = os.environ.get("PLAN_HISTORY_DIR")
            try:
                with _spawn_pool(max(2, config.JOB_SHIFT_POOL_SIZE), {"PLAN_HISTORY_DIR": history_dir}) as pool:
                    stored = _summary(rrp.run_solve_tasks(
                        rrp.shift_solve_tasks(self.planning_input, self.facility, None, "use", self.budget), pool=pool))
                    stored_files = list(Path(history_dir).glob("*.json"))
                    self.assertEqual(len(stored_files), len(self.planning_input["shift_keys"]), "one stored plan per shift")
                    slow = _summary(rrp.run_solve_tasks(
                        rrp.shift_solve_tasks(self.planning_input, self.facility, None, "use", slow_budget), pool=pool))
            finally:
                if previous is None:
                    os.environ.pop("PLAN_HISTORY_DIR", None)
                else:
                    os.environ["PLAN_HISTORY_DIR"] = previous

        print(f"\nPlan history guard (stored with {self.budget['solution_limit']} solutions, "
              f"re-solved with {slow_budget['solution_limit']}):")
        for shift in self.planning_input["shift_keys"]:
            print(f"  Shift {shift}: stored {stored[shift]['buses']} buses / {stored[shift]['km']:.1f} km -> "
                  f"slow run {slow[shift]['buses']} buses / {slow[shift]['km']:.1f} km")
            self.assertLessEqual(slow[shift]["buses"], stored[shift]["buses"])


if __name__ == "__main__":
    unittest.main()
