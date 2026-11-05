"""Transit planning, coordinate resolution, and roster endpoints."""

import json
import re
import uuid
from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..core.config import config
from ..core.auth import require_authenticated_user
from ..core.logging import logger
from ..db.repositories.stops_repo import stops_repo
from ..etl.extract.geoapify_client import geoapify_client
from ..etl.transform.coordinate_resolver import resolve_pickup_coordinates, resolve_roster_pickup_coordinates
from ..etl.transform.candidate_scoring import evaluate_coordinate_candidate
from ..etl.transform.text_cleaner_llm import text_cleaner_llm

router = APIRouter()


def _format_shift_start_time(start_minute: int) -> str:
    start_minute = int(start_minute) % 1440
    return f"{start_minute // 60:02d}:{start_minute % 60:02d}"


@router.get("/api/planning-options")
def planning_options(user: dict = Depends(require_authenticated_user)):
    """Return configured shift start times for the supervisor's time controls."""
    return {
        "arrival_buffer_minutes": config.COMMUTE.arrival_buffer_minutes,
        "shift_start_times": {
            shift: _format_shift_start_time(minutes)
            for shift, minutes in config.SHIFT_START_TIMES.items()
        }
    }


@router.post("/api/resolve_pickups")
def resolve_pickups(data: dict, user: dict = Depends(require_authenticated_user)):
    try:
        from .routes_dropoff import get_current_dropoff_data
        facility = get_current_dropoff_data(user["id"])
        if not facility.get("confirmed"):
            return JSONResponse({"error": "Choose and confirm a drop-off facility first."}, status_code=400)
        pickup_points = data.get("pickup_points", [])
        if not isinstance(pickup_points, list):
            return JSONResponse({"error": "Expected a pickup_points array."}, status_code=400)
        return {"locations": resolve_pickup_coordinates(pickup_points, facility=facility)}
    except Exception as e:
        logger.error(f"Error in /api/resolve_pickups: {e}", exc_info=True)
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500)


@router.post("/api/save_coordinate")
def save_coordinate(data: dict, user: dict = Depends(require_authenticated_user)):
    try:
        from .routes_dropoff import get_current_dropoff_data
        facility = get_current_dropoff_data(user["id"])
        if not facility.get("confirmed"):
            return JSONResponse({"status": "ERROR", "error": "Choose and confirm a drop-off facility first."}, status_code=400)
        stop_name = str(data.get("stop_name", "")).strip()
        lat = data.get("lat")
        lon = data.get("lon")
        reason = ""
        location_status = "LOCATION_UNVERIFIED"
        if not stop_name or lat is None or lon is None:
            return JSONResponse({"status": "ERROR", "error": "Missing stop_name, lat, or lon"}, status_code=400)
        try:
            lat, lon = float(lat), float(lon)
            location_status, reason = evaluate_coordinate_candidate(lat, lon, facility=facility)
            saved = location_status == "VERIFIED" and stops_repo.save_stop(stop_name, lat, lon)
        except (TypeError, ValueError):
            saved = False
        if saved:
            return {"status": "OK", "message": f"Saved {stop_name}"}
        error = reason if location_status != "VERIFIED" and reason else "Verified coordinate could not be saved."
        return JSONResponse({"status": "ERROR", "error": error}, status_code=400)
    except Exception as e:
        logger.error(f"Error in /api/save_coordinate: {e}", exc_info=True)
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500)


@router.post("/api/fetch_candidates")
def fetch_candidates(data: dict, user: dict = Depends(require_authenticated_user)):
    try:
        from .routes_dropoff import get_current_dropoff_data
        facility = get_current_dropoff_data(user["id"])
        if not facility.get("confirmed"):
            return JSONResponse({"error": "Choose and confirm a drop-off facility first."}, status_code=400)
        stop_name = str(data.get("stop_name", "")).strip()
        search_query = text_cleaner_llm.clean_search_text(stop_name)
        proximity = (float(facility["lat"]), float(facility["lon"]))
        return {"candidates": geoapify_client.geocode(search_query, proximity=proximity) or []}
    except Exception as e:
        logger.error(f"Error in /api/fetch_candidates: {e}", exc_info=True)
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500)


@router.post("/api/optimize_all")
async def optimize_all(request: Request, user: dict = Depends(require_authenticated_user)):
    return JSONResponse(
        {
            "status": "ERROR",
            "error": "This legacy endpoint does not run the OSRM-validated multi-shift planner. Upload an Excel roster to /api/plan_multi_shift.",
        },
        status_code=410,
    )


@router.post("/api/resolve_single")
def resolve_single(data: dict, user: dict = Depends(require_authenticated_user)):
    try:
        from .routes_dropoff import get_current_dropoff_data
        facility = get_current_dropoff_data(user["id"])
        if not facility.get("confirmed"):
            return JSONResponse({"status": "ERROR", "error": "Choose and confirm a drop-off facility first."}, status_code=400)
        stop_name = str(data.get("stop_name", "")).strip()
        lat = data.get("latitude")
        lon = data.get("longitude")
        reason = ""
        location_status = "LOCATION_UNVERIFIED"

        if lat is not None and lon is not None:
            try:
                latitude, longitude = float(lat), float(lon)
                location_status, reason = evaluate_coordinate_candidate(latitude, longitude, facility=facility)
                saved = location_status == "VERIFIED" and stops_repo.save_stop(stop_name, latitude, longitude)
            except (TypeError, ValueError):
                saved = False
            if saved:
                return {
                    "status": "OK",
                    "stop_name": stop_name,
                    "latitude": latitude,
                    "longitude": longitude,
                    "location_status": "VERIFIED",
                }
            error = reason if location_status != "VERIFIED" and reason else "Verified coordinate could not be saved."
            return JSONResponse({"status": "ERROR", "error": error}, status_code=400)

        resolved = resolve_pickup_coordinates([stop_name], facility=facility)
        return {"status": "OK", "stop_name": stop_name, **resolved.get(stop_name, {})}
    except Exception as e:
        logger.error(f"Error in /api/resolve_single: {e}", exc_info=True)
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500)


@router.post("/api/upload")
async def upload_file(request: Request, user: dict = Depends(require_authenticated_user)):
    return await plan_multi_shift(request, user)


@router.post("/api/plan_multi_shift")
async def plan_multi_shift(request: Request, user: dict = Depends(require_authenticated_user)):
    try:
        from .routes_dropoff import get_current_dropoff_data

        if "multipart/form-data" not in request.headers.get("content-type", ""):
            return JSONResponse(
                {"status": "ERROR", "error": "Upload an Excel roster before planning routes."},
                status_code=400,
            )

        form = await request.form()
        uploaded = form.get("file")
        if not isinstance(uploaded, StarletteUploadFile) or not uploaded.filename:
            return JSONResponse(
                {"status": "ERROR", "error": "No Excel roster was uploaded."},
                status_code=400,
            )
        if not uploaded.filename.lower().endswith((".xlsx", ".xls")):
            return JSONResponse(
                {"status": "ERROR", "error": "Upload a .xlsx or .xls roster."},
                status_code=400,
            )

        try:
            submitted_dropoff = json.loads(str(form.get("dropoff") or ""))
            submitted_lat = float(submitted_dropoff["lat"])
            submitted_lon = float(submitted_dropoff["lon"])
            facility_name = str(submitted_dropoff.get("name") or "Drop-off Facility").strip()
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            saved = get_current_dropoff_data(user["id"])
            if saved and saved.get("lat") is not None and saved.get("lon") is not None:
                submitted_lat = float(saved["lat"])
                submitted_lon = float(saved["lon"])
                facility_name = str(saved.get("name") or "Drop-off Facility").strip()
            else:
                return JSONResponse(
                    {"status": "ERROR", "error": "Choose and confirm a drop-off facility before planning routes."},
                    status_code=400,
                )

        if not (-90.0 <= submitted_lat <= 90.0 and -180.0 <= submitted_lon <= 180.0):
            return JSONResponse(
                {"status": "ERROR", "error": "Drop-off facility coordinates are invalid."},
                status_code=400,
            )

        facility = {
            "name": facility_name,
            "lat": submitted_lat,
            "lon": submitted_lon,
        }

        # Auto-sync confirmed facility for user in background
        try:
            from .routes_dropoff import _dropoff_file
            for uid in [user["id"], "supervisor"]:
                f_path, _ = _dropoff_file(uid)
                f_path.parent.mkdir(parents=True, exist_ok=True)
                with open(f_path, "w", encoding="utf-8") as f:
                    json.dump({
                        "name": facility_name,
                        "lat": submitted_lat,
                        "lon": submitted_lon,
                        "confirmed": True,
                        "confirmed_by": user.get("email") or "Supervisor",
                    }, f, indent=2)
        except Exception as save_err:
            logger.warning(f"Could not persist user dropoff file: {save_err}")

        shift_filter = str(form.get("shift_filter") or "ALL").strip().upper()
        if shift_filter != "ALL" and shift_filter not in config.SHIFT_START_TIMES:
            return JSONResponse(
                {"status": "ERROR", "error": "Choose All shifts or a valid shift."},
                status_code=400,
            )

        submitted_start_times = form.get("shift_start_times")
        shift_start_times = dict(config.SHIFT_START_TIMES)
        if submitted_start_times:
            try:
                parsed_start_times = json.loads(str(submitted_start_times))
            except json.JSONDecodeError:
                return JSONResponse({"status": "ERROR", "error": "Shift start times are invalid."}, status_code=400)
            if not isinstance(parsed_start_times, dict):
                return JSONResponse({"status": "ERROR", "error": "Shift start times are invalid."}, status_code=400)
            for shift, time_text in parsed_start_times.items():
                if shift not in shift_start_times or not isinstance(time_text, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_text):
                    return JSONResponse({"status": "ERROR", "error": "Choose a valid 24-hour start time for each shift."}, status_code=400)
                hours, minutes = (int(part) for part in time_text.split(":"))
                shift_start_times[shift] = hours * 60 + minutes

        from ..jobs.runner import submit_plan_job

        user_output_dir = config.OUTPUT_DIR / "history" / user["id"]
        user_input_dir = config.DATA_DIR / "input" / user["id"]
        user_output_dir.mkdir(parents=True, exist_ok=True)
        user_input_dir.mkdir(parents=True, exist_ok=True)
        contents = await uploaded.read()
        # Each job gets its own input/output files so concurrent builds never overwrite each other.
        upload_id = uuid.uuid4().hex
        raw_path = user_input_dir / f"roster_{upload_id}.xlsx"
        await run_in_threadpool(raw_path.write_bytes, contents)
        # Legacy location still read by /api/evaluate_scenarios.
        await run_in_threadpool((user_input_dir / "uploaded_roster.xlsx").write_bytes, contents)

        job = submit_plan_job(
            user=user,
            raw_path=raw_path,
            job_out_path=user_output_dir / f"roster_{upload_id}.xlsx",
            latest_out_path=user_output_dir / "LATEST.xlsx",
            facility=facility,
            shift_filter=shift_filter,
            shift_start_times=shift_start_times,
            uploaded_filename=uploaded.filename or "Uploaded roster.xlsx",
            pickup_resolver=resolve_roster_pickup_coordinates,
        )
        return JSONResponse(
            {
                "status": "QUEUED",
                "job_id": job["job_id"],
                "kind": job["kind"],
                "poll_url": f"/api/plan_jobs/{job['job_id']}",
            },
            status_code=202,
        )
    except Exception as e:
        logger.error(f"Error in /api/plan_multi_shift: {e}", exc_info=True)
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500)


@router.get("/api/download_final_roster")
async def download_final_roster(user: dict = Depends(require_authenticated_user)):
    out_path = config.OUTPUT_DIR / "history" / user["id"] / "LATEST.xlsx"
    if not out_path.exists():
        return JSONResponse(
            {"status": "ERROR", "error": "Upload a roster and plan routes before downloading."},
            status_code=404,
        )

    return FileResponse(
        out_path,
        filename="FINAL_OPTIMIZED_BUS_ROSTER.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/api/evaluate_scenarios")
async def evaluate_scenarios_endpoint(request: Request, user: dict = Depends(require_authenticated_user)):
    try:
        from run_route_planner import evaluate_all_scenarios, load_all_verified_stops
        from .routes_dropoff import get_current_dropoff_data
        import pandas as pd

        raw_path = config.DATA_DIR / "input" / user["id"] / "uploaded_roster.xlsx"
        if not raw_path.exists():
            return JSONResponse({"status": "ERROR", "error": "Upload an Excel roster first."}, status_code=400)

        facility = get_current_dropoff_data(user["id"])
        if not facility or not facility.get("confirmed"):
            return JSONResponse({"status": "ERROR", "error": "Confirm drop-off facility first."}, status_code=400)

        stop_lookup = load_all_verified_stops()
        df = pd.read_excel(raw_path, header=1).dropna(how="all").reset_index(drop=True)
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

        report = await run_in_threadpool(
            evaluate_all_scenarios,
            all_employees,
            stop_lookup,
            facility,
            shift_start_times=dict(config.SHIFT_START_TIMES),
            shift_filter="ALL",
        )
        return {"status": "SUCCESS", "scenarios": report}
    except Exception as e:
        logger.error(f"Error in /api/evaluate_scenarios: {e}", exc_info=True)
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500)


def _job_response(job_id: str, user: dict, kind: str):
    from ..jobs.runner import job_store
    job = job_store.get(job_id, user["id"], kind=kind)
    if job is None:
        return JSONResponse(
            {
                "status": "ERROR",
                "error": "Job not found. It may have expired or the server restarted; run it again.",
            },
            status_code=404,
        )
    return job


@router.get("/api/plan_jobs/{job_id}")
def get_plan_job(job_id: str, user: dict = Depends(require_authenticated_user)):
    """Status, progress, timings and (when succeeded) the plan result of a plan job."""
    return _job_response(job_id, user, "plan")


@router.post("/api/scenarios/run")
async def run_scenarios(request: Request, user: dict = Depends(require_authenticated_user)):
    """Start a scenario job on the same roster, geocoding and start times as a plan job."""
    from ..jobs.runner import job_store, submit_scenarios_job

    try:
        body = await request.json()
    except Exception:
        body = {}
    plan_job_id = str((body or {}).get("plan_job_id") or "").strip() or job_store.latest(user["id"], "plan", "succeeded")
    if not plan_job_id:
        return JSONResponse({"status": "ERROR", "error": "Plan routes first; scenarios reuse that plan's roster."}, status_code=400)
    job = submit_scenarios_job(user, plan_job_id)
    if job is None:
        return JSONResponse(
            {"status": "ERROR", "error": "That plan is not finished or is no longer available; plan routes again."},
            status_code=409,
        )
    return JSONResponse(
        {
            "status": "QUEUED",
            "job_id": job["job_id"],
            "kind": job["kind"],
            "plan_job_id": plan_job_id,
            "poll_url": f"/api/scenarios/{job['job_id']}",
        },
        status_code=202,
    )


@router.get("/api/scenarios/{job_id}")
def get_scenarios_job(job_id: str, user: dict = Depends(require_authenticated_user)):
    """Status, progress, timings and (when succeeded) the scenario comparison."""
    return _job_response(job_id, user, "scenarios")
