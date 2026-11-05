"""Measure the background-job API end to end, in-process (no server, no Clerk).

POSTs a roster to /api/plan_multi_shift, polls /api/plan_jobs/{id}, then runs
/api/scenarios/run on the same plan and polls /api/scenarios/{id}. Prints time to
job_id, total job time, per-shift solve times and the scenario table.

Side effects are contained: auth is overridden with a throwaway user, roster
history is not archived (no Supabase write), and the shared 'supervisor' drop-off
file is restored afterwards.
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.api import routes_history  # noqa: E402
from backend.app.api.routes_dropoff import _dropoff_file  # noqa: E402
from backend.app.core.auth import require_authenticated_user  # noqa: E402
from backend.app.core.config import config  # noqa: E402
from backend.app.main import app  # noqa: E402

MEASURE_USER = {"id": "measure_jobs_user", "email": "measure@localhost"}


def poll(client, url, label):
    started = time.perf_counter()
    last_stage = None
    while True:
        job = client.get(url).json()
        stage = (job.get("progress") or {}).get("stage")
        if stage != last_stage:
            p = job.get("progress") or {}
            print(f"  [{time.perf_counter() - started:6.1f}s] {label}: {job.get('status')} - {stage} ({p.get('done')}/{p.get('total')})")
            last_stage = stage
        if job.get("status") in {"succeeded", "failed"}:
            return job, time.perf_counter() - started
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=PROJECT_ROOT / "tests" / "fixtures" / "sample_roster.xlsx")
    parser.add_argument("--json-out", type=Path, default=None, help="Write the full plan + scenario job JSON here")
    args = parser.parse_args()

    facility = {"name": config.FACILITY.name, "lat": config.FACILITY.lat, "lon": config.FACILITY.lon}
    start_times = {s: f"{m // 60:02d}:{m % 60:02d}" for s, m in config.SHIFT_START_TIMES.items()}

    supervisor_file, _ = _dropoff_file("supervisor")
    backup = supervisor_file.read_bytes() if supervisor_file.exists() else None
    app.dependency_overrides[require_authenticated_user] = lambda: MEASURE_USER
    routes_history.save_roster_history = lambda data, user: {"record": None}
    try:
        with TestClient(app) as client:
            print(f"Roster: {args.roster.name} | pool size {config.JOB_SHIFT_POOL_SIZE} | "
                  f"budget {config.SOLVER_SOLUTION_LIMIT} solutions / {config.JOB_SOLVER_TIME_LIMIT_SECONDS}s cap")
            t0 = time.perf_counter()
            with open(args.roster, "rb") as f:
                res = client.post(
                    "/api/plan_multi_shift",
                    files={"file": (args.roster.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    data={"dropoff": json.dumps(facility), "shift_filter": "ALL", "shift_start_times": json.dumps(start_times)},
                )
            time_to_job_id = time.perf_counter() - t0
            queued = res.json()
            print(f"POST /api/plan_multi_shift -> HTTP {res.status_code} {queued.get('status')} in {time_to_job_id * 1000:.0f} ms")
            plan_job, plan_wall = poll(client, queued["poll_url"], "plan")
            if plan_job["status"] != "succeeded":
                raise SystemExit(f"Plan job failed: {plan_job.get('error')}")
            t = plan_job["timings"]
            print(f"Plan job: {plan_job['status']} in {t['total_sec']:.1f}s (queued {t['queued_sec']}s, roster+geocode {t['prepare_sec']}s, "
                  f"solve wall {t['solve_wall_sec']}s, workbook+history {t['finalize_sec']}s)")
            for shift in plan_job["result"]["shifts"]:
                print(f"  Shift {shift['shift']}: {shift['buses_used']} buses, {shift['total_distance_km']} km, "
                      f"solve {t['shift_solve_sec'][shift['shift']]}s, reproducible={shift['optimization']['reproducible']}")

            t1 = time.perf_counter()
            res = client.post("/api/scenarios/run", json={"plan_job_id": queued["job_id"]})
            sc_queued = res.json()
            print(f"POST /api/scenarios/run -> HTTP {res.status_code} {sc_queued.get('status')} in {(time.perf_counter() - t1) * 1000:.0f} ms")
            sc_job, _ = poll(client, sc_queued["poll_url"], "scenarios")
            if sc_job["status"] != "succeeded":
                raise SystemExit(f"Scenario job failed: {sc_job.get('error')}")
            print(f"Scenario job: {sc_job['status']} in {sc_job['timings']['total_sec']:.1f}s "
                  f"(solve wall {sc_job['timings']['solve_wall_sec']}s)")
            table = sc_job["result"]["scenarios"]["comparison_table"]
            shifts = sorted({k for row in table for k in row.get("buses_by_shift", {})})
            print(f"\n{'SCENARIO':<46} | " + " | ".join(f"{s:>2}" for s in shifts) + " | TOTAL | MAX RIDE | WINDOW | CLIENT RULE")
            for row in table:
                per = " | ".join(f"{row.get('buses_by_shift', {}).get(s, '?'):>2}" for s in shifts)
                print(f"{row['scenario_name']:<46} | {per} | {row['buses']:<5} | {row['max_ride_min']:<8} | "
                      f"{row['max_first_pickup_before_shift_min']:<6} | {row.get('client_rule_status', '(baseline row)')}")
            if args.json_out:
                args.json_out.write_text(json.dumps({"plan": plan_job, "scenarios": sc_job}, default=str), encoding="utf-8")
    finally:
        app.dependency_overrides.clear()
        if backup is not None:
            supervisor_file.write_bytes(backup)
        own_file, _ = _dropoff_file(MEASURE_USER["id"])
        own_file.unlink(missing_ok=True)
        shutil.rmtree(config.DATA_DIR / "input" / MEASURE_USER["id"], ignore_errors=True)
        shutil.rmtree(config.OUTPUT_DIR / "history" / MEASURE_USER["id"], ignore_errors=True)


if __name__ == "__main__":
    main()
