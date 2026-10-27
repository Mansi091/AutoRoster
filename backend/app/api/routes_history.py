"""Private roster history stored in Supabase and owned by Clerk user ID."""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response

from ..core.auth import require_authenticated_user
from ..core.config import config

router = APIRouter()
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
HISTORY_TABLE = "roster_history"
DEFAULT_BUCKET = "roster-history"
SUPABASE_TIMEOUT_SECONDS = 30


def _supabase_config() -> tuple[str, str, str]:
    import os

    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    # Never use the publishable/anon key for server-side history or file storage.
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.getenv("SUPABASE_ROSTER_BUCKET", DEFAULT_BUCKET).strip()
    if not url or not key or not bucket:
        raise HTTPException(
            status_code=503,
            detail="Supabase roster history is not configured. Add the server-side key and bucket settings.",
        )
    return url, key, bucket


def _headers(key: str, *, content_type: str | None = None) -> dict[str, str]:
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _check_supabase(response: requests.Response, action: str) -> None:
    if response.ok:
        return
    # Supabase error bodies can expose database details; keep API errors concise.
    raise HTTPException(status_code=502, detail=f"Supabase could not {action} roster history.")


def _storage_object_url(url: str, bucket: str, object_path: str) -> str:
    safe_bucket = quote(bucket, safe="")
    safe_path = quote(object_path, safe="/")
    return f"{url}/storage/v1/object/{safe_bucket}/{safe_path}"


@router.post("/api/history/save")
def save_roster_history(
    data: dict = Body(...),
    user: dict = Depends(require_authenticated_user),
):
    url, key, bucket = _supabase_config()
    user_id = user["id"]
    latest_workbook = config.OUTPUT_DIR / "history" / user_id / "LATEST.xlsx"
    if not latest_workbook.is_file():
        raise HTTPException(status_code=409, detail="Run route planning before saving a roster to history.")

    job_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_filename = f"ROSTER_{timestamp}_{job_id[:8]}.xlsx"
    object_path = f"{user_id}/{archive_filename}"
    object_url = _storage_object_url(url, bucket, object_path)

    try:
        with latest_workbook.open("rb") as workbook:
            upload_response = requests.post(
                object_url,
                headers={
                    **_headers(key, content_type=XLSX_MEDIA_TYPE),
                    "x-upsert": "false",
                },
                data=workbook,
                timeout=SUPABASE_TIMEOUT_SECONDS,
            )
        _check_supabase(upload_response, "upload")

        row = {
            "id": job_id,
            "clerk_user_id": user_id,
            "uploaded_file_name": str(data.get("uploaded_file_name") or ""),
            "total_employees": int(data.get("total_employees") or 0),
            "routed_employees": int(data.get("routed_employees") or 0),
            "total_routes": int(data.get("total_routes") or 0),
            "destination_name": str(data.get("destination_name") or ""),
            "shifts_summary": data.get("shifts_summary") or [],
            "workbook_filename": archive_filename,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        insert_response = requests.post(
            f"{url}/rest/v1/{HISTORY_TABLE}",
            headers={**_headers(key, content_type="application/json"), "Prefer": "return=representation"},
            json=row,
            timeout=SUPABASE_TIMEOUT_SECONDS,
        )
        _check_supabase(insert_response, "save")
        inserted_rows = insert_response.json()
        saved_row = inserted_rows[0] if inserted_rows else row
        return {"status": "SUCCESS", "record": saved_row}
    except HTTPException:
        # Do not leave an orphaned workbook if its metadata could not be saved.
        try:
            requests.delete(object_url, headers=_headers(key), timeout=SUPABASE_TIMEOUT_SECONDS)
        except requests.RequestException:
            pass
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Could not reach Supabase to save this roster.") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Roster history contains invalid summary data.") from exc


@router.get("/api/history/list")
def list_history(user: dict = Depends(require_authenticated_user)):
    url, key, _bucket = _supabase_config()
    try:
        response = requests.get(
            f"{url}/rest/v1/{HISTORY_TABLE}",
            headers=_headers(key),
            params={
                "select": "id,uploaded_file_name,total_employees,routed_employees,total_routes,destination_name,workbook_filename,created_at",
                "clerk_user_id": f"eq.{user['id']}",
                "order": "created_at.desc",
            },
            timeout=SUPABASE_TIMEOUT_SECONDS,
        )
        _check_supabase(response, "load")
        return {"status": "SUCCESS", "history": response.json()}
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Could not reach Supabase to load roster history.") from exc


@router.get("/api/history/download/{filename}")
def download_historical_roster(filename: str, user: dict = Depends(require_authenticated_user)):
    safe_filename = Path(filename).name
    if safe_filename != filename or not safe_filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=404, detail="Roster not found in your history.")

    url, key, bucket = _supabase_config()
    try:
        record_response = requests.get(
            f"{url}/rest/v1/{HISTORY_TABLE}",
            headers=_headers(key),
            params={
                "select": "workbook_filename",
                "clerk_user_id": f"eq.{user['id']}",
                "workbook_filename": f"eq.{safe_filename}",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT_SECONDS,
        )
        _check_supabase(record_response, "find")
        records = record_response.json()
        if not records:
            raise HTTPException(status_code=404, detail="Roster not found in your history.")

        object_path = f"{user['id']}/{safe_filename}"
        file_response = requests.get(
            _storage_object_url(url, bucket, object_path),
            headers=_headers(key),
            timeout=SUPABASE_TIMEOUT_SECONDS,
        )
        if file_response.status_code == 404:
            raise HTTPException(status_code=404, detail="This archived roster file is no longer available.")
        _check_supabase(file_response, "download")
        return Response(
            content=file_response.content,
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Could not reach Supabase to download this roster.") from exc
