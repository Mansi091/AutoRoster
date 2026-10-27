"""Transit planning, coordinate resolution, and roster endpoints."""

import json
import re
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


def _format_shift_start_time(arrival_deadline: int) -> str:
    start_minute = (arrival_deadline + config.COMMUTE.arrival_buffer_minutes) % 1440
    return f"{start_minute // 60:02d}:{start_minute % 60:02d}"


@router.get("/api/planning-options")
def planning_options(user: dict = Depends(require_authenticated_user)):
    """Return configured shift start times for the supervisor's time controls."""
    return {
        "arrival_buffer_minutes": config.COMMUTE.arrival_buffer_minutes,
        "shift_start_times": {
            shift: _format_shift_start_time(minutes)
            for shift, minutes in config.SHIFT_ARRIVAL_DEADLINES.items()
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
        from run_route_planner import run_full_pipeline
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
        if shift_filter != "ALL" and shift_filter not in config.SHIFT_ARRIVAL_DEADLINES:
            return JSONResponse(
                {"status": "ERROR", "error": "Choose All shifts or a valid shift."},
                status_code=400,
            )

        submitted_start_times = form.get("shift_start_times")
        shift_deadlines = dict(config.SHIFT_ARRIVAL_DEADLINES)
        if submitted_start_times:
            try:
                parsed_start_times = json.loads(str(submitted_start_times))
            except json.JSONDecodeError:
                return JSONResponse({"status": "ERROR", "error": "Shift start times are invalid."}, status_code=400)
            if not isinstance(parsed_start_times, dict):
                return JSONResponse({"status": "ERROR", "error": "Shift start times are invalid."}, status_code=400)
            for shift, time_text in parsed_start_times.items():
                if shift not in shift_deadlines or not isinstance(time_text, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_text):
                    return JSONResponse({"status": "ERROR", "error": "Choose a valid 24-hour start time for each shift."}, status_code=400)
                hours, minutes = (int(part) for part in time_text.split(":"))
                shift_deadlines[shift] = (hours * 60 + minutes - config.COMMUTE.arrival_buffer_minutes) % 1440

        user_output_dir = config.OUTPUT_DIR / "history" / user["id"]
        user_output_dir.mkdir(parents=True, exist_ok=True)
        raw_path = config.DATA_DIR / "input" / user["id"] / "uploaded_roster.xlsx"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        contents = await uploaded.read()
        await run_in_threadpool(raw_path.write_bytes, contents)

        out_path = user_output_dir / "LATEST.xlsx"
        results = await run_in_threadpool(
            run_full_pipeline,
            raw_path,
            out_path,
            facility,
            resolve_roster_pickup_coordinates,
            shift_deadlines,
            shift_filter,
        )
        all_routes = [route for shift in results for route in shift.get("routes", [])]

        # Automatically archive to history immediately upon generation
        history_record = None
        try:
            from .routes_history import save_roster_history
            total_emp = sum(s.get("total_employees_in_roster", 0) for s in results)
            routed_emp = sum(s.get("assigned_passengers", 0) for s in results)
            archive_data = {
                "uploaded_file_name": uploaded.filename or "Uploaded roster.xlsx",
                "total_employees": total_emp or len(all_routes) * 10,
                "routed_employees": routed_emp or len(all_routes) * 10,
                "total_routes": len(all_routes),
                "destination_name": facility.get("name", "Drop-off Facility"),
                "shifts_summary": results,
            }
            res_history = save_roster_history(data=archive_data, user=user)
            history_record = res_history.get("record")
        except Exception as hist_err:
            logger.warning(f"Auto-saving roster history failed: {hist_err}")

        return {
            "status": "SUCCESS",
            "shifts": results,
            "routes": all_routes,
            "excel_available": out_path.exists(),
            "excel_filename": "FINAL_OPTIMIZED_BUS_ROSTER.xlsx",
            "history_record": history_record,
        }
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
