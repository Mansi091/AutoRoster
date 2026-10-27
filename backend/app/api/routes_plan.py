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
        if (not facility or not facility.get("confirmed")) and data.get("facility"):
            facility = data.get("facility")
        stop_name = str(data.get("stop_name", "")).strip()
        lat = data.get("lat")
        lon = data.get("lon")
        if not stop_name or lat is None or lon is None:
            return JSONResponse({"status": "ERROR", "error": "Missing stop_name, lat, or lon"}, status_code=400)
        try:
            lat, lon = float(lat), float(lon)
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                return JSONResponse({"status": "ERROR", "error": "Coordinates out of bounds (-90..90, -180..180)."}, status_code=400)
            saved = stops_repo.save_stop(stop_name, lat, lon)
            if not saved:
                stops_repo._stops[stop_name] = (lat, lon)
                from ...utils.text import normalize_coordinate_key
                stops_repo._normalized_index[normalize_coordinate_key(stop_name)] = (lat, lon)
                saved = True
        except (TypeError, ValueError):
            saved = False
        if saved:
            return {"status": "OK", "message": f"Saved {stop_name}", "stop_name": stop_name, "latitude": lat, "longitude": lon}
        return JSONResponse({"status": "ERROR", "error": "Verified coordinate could not be saved."}, status_code=400)
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

        saved_dropoff = get_current_dropoff_data(user["id"])
        try:
            submitted_dropoff = json.loads(str(form.get("dropoff") or ""))
            submitted_lat = float(submitted_dropoff["lat"])
            submitted_lon = float(submitted_dropoff["lon"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return JSONResponse(
                {"status": "ERROR", "error": "Choose and confirm a drop-off facility before planning routes."},
                status_code=400,
            )

        if (
            not saved_dropoff
            or not saved_dropoff.get("confirmed")
            or saved_dropoff.get("lat") is None
            or saved_dropoff.get("lon") is None
            or abs(float(saved_dropoff["lat"]) - submitted_lat) > 1e-7
            or abs(float(saved_dropoff["lon"]) - submitted_lon) > 1e-7
        ):
            return JSONResponse(
                {"status": "ERROR", "error": "Choose and confirm a drop-off facility before planning routes."},
                status_code=400,
            )

        dropoff = saved_dropoff
        facility = {
            "name": dropoff.get("name") or "Drop-off Facility",
            "lat": float(dropoff["lat"]),
            "lon": float(dropoff["lon"]),
        }

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
        )
        all_routes = [route for shift in results for route in shift.get("routes", [])]

        return {
            "status": "SUCCESS",
            "shifts": results,
            "routes": all_routes,
            "excel_available": out_path.exists(),
            "excel_filename": "FINAL_OPTIMIZED_BUS_ROSTER.xlsx",
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
