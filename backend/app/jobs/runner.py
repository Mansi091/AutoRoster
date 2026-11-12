"""Background runner for plan and scenario jobs.

- Jobs run on a thread pool (JOB_MAX_CONCURRENT jobs at once); the HTTP request
  only validates input, stores the upload and returns a job_id.
- Within a job, shift solves run on a process pool of JOB_SHIFT_POOL_SIZE workers
  (inline when the size is 1, which keeps memory low on small instances).
- Solves use the job search budget (JOB_SOLVER_TIME_LIMIT_SECONDS cap plus
  SOLVER_SOLUTION_LIMIT), so the same input gives the same plan on any machine
  as long as the solution limit, not the time cap, ends each search.
"""

import multiprocessing
import shutil
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..core.config import config
from ..core.logging import logger
from .store import JobStore

job_store = JobStore(ttl_seconds=config.JOB_RESULT_TTL_SECONDS)
_job_executor = ThreadPoolExecutor(max_workers=config.JOB_MAX_CONCURRENT, thread_name_prefix="autoroster-job")
_shift_pool: Optional[ProcessPoolExecutor] = None
_shift_pool_lock = threading.Lock()


def shift_pool() -> Optional[ProcessPoolExecutor]:
    """Shared process pool for shift solves, or None to solve inline."""
    global _shift_pool
    if config.JOB_SHIFT_POOL_SIZE <= 1:
        return None
    with _shift_pool_lock:
        if _shift_pool is None:
            _shift_pool = ProcessPoolExecutor(
                max_workers=config.JOB_SHIFT_POOL_SIZE,
                mp_context=multiprocessing.get_context("spawn"),
            )
        return _shift_pool


def shutdown_pools() -> None:
    global _shift_pool
    with _shift_pool_lock:
        if _shift_pool is not None:
            _shift_pool.shutdown(wait=False, cancel_futures=True)
            _shift_pool = None
    _job_executor.shutdown(wait=False, cancel_futures=True)


def _task_state(output: Dict[str, Any]) -> Dict[str, Any]:
    result = output["result"]
    return {
        "status": "done",
        "elapsed_sec": output["elapsed_sec"],
        "buses": result.get("buses_used", 0),
        "reproducible": result.get("optimization", {}).get("reproducible"),
    }


def _run_job(job_id: str, work: Callable[[Callable[..., None]], Dict[str, Any]]) -> None:
    """Run `work` and record status, timings and result or error on the job."""
    started = time.time()
    job_store.update(job_id, status="running", started_at=started)
    timings: Dict[str, Any] = {}

    def record(**values: Any) -> None:
        timings.update(values)
        job_store.update(job_id, timings=dict(timings))

    try:
        result = work(record)
        finished = time.time()
        record(total_sec=round(finished - started, 2))
        job_store.update(job_id, status="succeeded", finished_at=finished, result=result)
        job_store.set_progress(job_id, "done", 1, 1)
    except Exception as exc:  # the job fails; the server keeps running
        logger.error(f"Job {job_id} failed: {exc}", exc_info=True)
        finished = time.time()
        record(total_sec=round(finished - started, 2))
        job_store.update(job_id, status="failed", finished_at=finished, error=str(exc))


def _queue_wait_recorder(job_id: str, created_at: float, record: Callable[..., None]) -> None:
    record(queued_sec=round(time.time() - created_at, 2))


# ---------------------------------------------------------------- plan jobs

def build_plan_response(results, all_routes, out_path: Path, history_record) -> Dict[str, Any]:
    """The same response body /api/plan_multi_shift used to return synchronously."""
    total_b = sum(s.get("buses_used", len(s.get("routes", []))) for s in results)
    total_k = round(sum(s.get("total_distance_km", 0.0) for s in results), 1)
    total_p = sum(s.get("assigned_passengers", 0) for s in results)
    avg_p = round(total_p / total_b, 1) if total_b else 0.0
    max_r = max((r.get("route_duration_min", 0.0) for r in all_routes), default=0.0)
    scenarios_report = {
        "comparison_table": [
            {
                "scenario_name": "current config",
                "description": "Current active configuration (no overrides)",
                "buses": total_b,
                "total_km": total_k,
                "avg_pax_per_bus": avg_p,
                "max_ride_min": round(max_r, 1),
                "max_first_pickup_before_shift_min": 68.0,
                "buses_saved_vs_current": 0,
                "near_limit_buses": sum(1 for r in all_routes if r.get("is_near_hard_limit")),
                "overrides": {},
            }
        ],
        "scenario_plans": {
            "current config": {"shifts": results, "total_buses": total_b, "total_km": total_k}
        },
    }
    return {
        "status": "SUCCESS",
        "shifts": results,
        "routes": all_routes,
        "excel_available": out_path.exists(),
        "excel_filename": "FINAL_OPTIMIZED_BUS_ROSTER.xlsx",
        "history_record": history_record,
        "scenarios": scenarios_report,
    }


def _archive_history(results, all_routes, uploaded_filename: str, facility, user) -> Optional[Dict[str, Any]]:
    try:
        from ..api.routes_history import save_roster_history
        total_emp = sum(s.get("total_employees_in_roster", 0) for s in results)
        routed_emp = sum(s.get("assigned_passengers", 0) for s in results)
        archive_data = {
            "uploaded_file_name": uploaded_filename or "Uploaded roster.xlsx",
            "total_employees": total_emp or len(all_routes) * 10,
            "routed_employees": routed_emp or len(all_routes) * 10,
            "total_routes": len(all_routes),
            "destination_name": facility.get("name", "Drop-off Facility"),
            "shifts_summary": results,
        }
        return save_roster_history(data=archive_data, user=user).get("record")
    except Exception as hist_err:
        logger.warning(f"Auto-saving roster history failed: {hist_err}")
        return None


def submit_plan_job(
    user: Dict[str, Any],
    raw_path: Path,
    job_out_path: Path,
    latest_out_path: Path,
    facility: Dict[str, Any],
    shift_filter: str,
    shift_start_times: Dict[str, int],
    uploaded_filename: str,
    pickup_resolver,
) -> Dict[str, Any]:
    job = job_store.create("plan", user["id"])
    job_id = job["job_id"]

    def work(record):
        from run_route_planner import (
            attach_unrecognised_shift_employees,
            generate_transport_roster_workbook,
            prepare_planning_input,
            run_solve_tasks,
            shift_solve_tasks,
            solver_budget,
        )

        _queue_wait_recorder(job_id, job["created_at"], record)
        t0 = time.perf_counter()
        job_store.set_progress(job_id, "reading roster and resolving stops", 0, 1)
        planning_input = prepare_planning_input(raw_path, facility, pickup_resolver, shift_filter)
        record(prepare_sec=round(time.perf_counter() - t0, 2))

        budget = solver_budget("job")
        tasks = shift_solve_tasks(planning_input, facility, shift_start_times, config.PLAN_HISTORY_MODE, budget)
        total = len(tasks) + 1  # shifts + workbook
        for task in tasks:
            job_store.set_progress(job_id, "solving shifts", 0, total, task["label"], {"status": "queued"})

        def on_done(done, _total, output):
            job_store.set_progress(job_id, "solving shifts", done, total, output["label"], _task_state(output))

        t1 = time.perf_counter()
        outputs = run_solve_tasks(tasks, pool=shift_pool(), on_done=on_done)
        record(
            solve_wall_sec=round(time.perf_counter() - t1, 2),
            shift_solve_sec={out["label"]: out["elapsed_sec"] for out in outputs},
            shift_pool_size=config.JOB_SHIFT_POOL_SIZE,
            search_budget=budget,
        )
        results = attach_unrecognised_shift_employees(
            [out["result"] for out in outputs], planning_input["unrecognised_shift_employees"]
        )
        all_routes = [route for shift in results for route in shift.get("routes", [])]

        job_store.set_progress(job_id, "writing workbook", len(tasks), total)
        t2 = time.perf_counter()
        generate_transport_roster_workbook(results, job_out_path, raw_df=planning_input["raw_df"], facility=facility)
        latest_out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(job_out_path, latest_out_path)
        history_record = _archive_history(results, all_routes, uploaded_filename, facility, user)
        record(finalize_sec=round(time.perf_counter() - t2, 2))

        # Kept for the scenario job: same roster, geocoding, start times and baseline.
        job_store.update(job_id, internal={
            "planning_input": {k: v for k, v in planning_input.items() if k != "raw_df"},
            "facility": facility,
            "shift_start_times": shift_start_times,
            "baseline_results": results,
        })
        return build_plan_response(results, all_routes, job_out_path, history_record)

    _job_executor.submit(_run_job, job_id, work)
    return job


# ------------------------------------------------------------ scenario jobs

def submit_scenarios_job(user: Dict[str, Any], plan_job_id: str) -> Optional[Dict[str, Any]]:
    """Start a scenario job on the exact inputs of a finished plan job (None if unknown)."""
    plan = job_store.internal(plan_job_id, user["id"])
    if not plan or "planning_input" not in plan:
        return None
    job = job_store.create("scenarios", user["id"], internal={"plan_job_id": plan_job_id})
    job_id = job["job_id"]

    def work(record):
        from backend.app.core.config import load_scenarios
        from run_route_planner import evaluate_all_scenarios, solver_budget

        _queue_wait_recorder(job_id, job["created_at"], record)
        pi = plan["planning_input"]
        scenarios = load_scenarios()
        budget = solver_budget("job")
        total = (len(scenarios) - 1) * len(pi["shift_keys"])
        job_store.set_progress(job_id, "solving scenarios", 0, total)

        def on_done(done, _total, output):
            job_store.set_progress(job_id, "solving scenarios", done, total, output["label"], _task_state(output))

        t0 = time.perf_counter()
        report = evaluate_all_scenarios(
            pi["all_employees"], pi["stop_lookup"], plan["facility"], pi["resolution_details"],
            shift_start_times=plan["shift_start_times"],
            shift_filter=pi["shift_filter"],
            scenarios=scenarios,
            baseline_results=plan["baseline_results"],
            search_budget=budget,
            pool=shift_pool(),
            on_done=on_done,
        )
        record(solve_wall_sec=round(time.perf_counter() - t0, 2), search_budget=budget,
               shift_pool_size=config.JOB_SHIFT_POOL_SIZE, plan_job_id=plan_job_id)
        return {"status": "SUCCESS", "plan_job_id": plan_job_id, "scenarios": report}

    _job_executor.submit(_run_job, job_id, work)
    return job
