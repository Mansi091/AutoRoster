"""Drop-off location search and supervisor confirmation endpoints."""

import json
import math
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote_plus, urlparse

import requests
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from ..core.config import config
from ..core.auth import require_authenticated_user
from ..core.logging import logger
from ..utils.geo import is_within_pune_geofence

router = APIRouter()
DROPOFF_DIR = config.DATA_DIR / "dropoff_locations"
DEFAULT_DROPOFF = {
    "name": None,
    "address": None,
    "lat": None,
    "lon": None,
    "confirmed": False,
    "confirmed_by": None,
    "updated_at": None,
}


def _dropoff_file(user_id: str):
    safe_user_id = re.sub(r"[^A-Za-z0-9_-]", "_", user_id)
    return DROPOFF_DIR / f"{safe_user_id}.json", safe_user_id


def get_current_dropoff_data(user_id: str):
    dropoff_file, safe_user_id = _dropoff_file(user_id)
    if dropoff_file.exists():
        try:
            with open(dropoff_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if (data.get("supervisor_id") == safe_user_id and data.get("confirmed")
                        and data.get("lat") is not None and data.get("lon") is not None
                        and is_within_pune_geofence(float(data["lat"]), float(data["lon"]))):
                    return data
        except Exception as e:
            logger.warning(f"Failed to read supervisor drop-off file: {e}")
    return DEFAULT_DROPOFF


def _google_maps_location(query: str):
    """Turn a pasted Google Maps place URL or coordinate pair into a result."""
    parsed = urlparse(query)
    is_maps_url = parsed.netloc.lower().endswith("google.com") and "/maps" in parsed.path
    name = ""
    if is_maps_url:
        place_match = re.search(r"/maps/place/([^/]+)", parsed.path, flags=re.IGNORECASE)
        if place_match:
            name = unquote_plus(place_match.group(1)).replace("+", " ").strip()

    coord_patterns = (
        r"@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)",
        r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
    )
    coordinates = None
    for pattern in coord_patterns:
        match = re.search(pattern, query)
        if match:
            coordinates = (float(match.group(1)), float(match.group(2)))
            break

    if coordinates is None and is_maps_url:
        params = parse_qs(parsed.query)
        for key in ("query", "q", "destination"):
            value = params.get(key, [""])[0]
            match = re.search(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", value)
            if match:
                coordinates = (float(match.group(1)), float(match.group(2)))
                break

    # Also accept a plain latitude,longitude pair, useful when copied from a map.
    if coordinates is None and not is_maps_url:
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", query)
        if match:
            coordinates = (float(match.group(1)), float(match.group(2)))

    if coordinates is not None:
        lat, lon = coordinates
        if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None, name
        label = name or "Pinned map location"
        return [{
            "name": label,
            "address": f"Google Maps pin · {lat:.6f}, {lon:.6f}",
            "lat": lat,
            "lon": lon,
            "source": "Google Maps link",
        }], name

    return None, name


@router.get("/api/dropoff/current")
def get_current_dropoff(user: dict = Depends(require_authenticated_user)):
    return get_current_dropoff_data(user["id"])


@router.get("/api/dropoff/search")
def search_dropoff(q: str = Query(default=""), user: dict = Depends(require_authenticated_user)):
    query = q.strip()
    if not query:
        return []

    map_candidates, map_place_name = _google_maps_location(query)
    if map_candidates is not None:
        return map_candidates
    # If a pasted Google Maps URL has no embedded pin coordinates, search its
    # place title instead of sending the full URL to Places.
    search_query = map_place_name or query
    if not config.GOOGLE_MAPS_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Google Places search is not configured. Add GOOGLE_MAPS_API_KEY to the project .env and restart the backend.",
        )

    try:
        res = requests.post(
            "https://places.googleapis.com/v1/places:searchText",
            json={
                "textQuery": search_query,
                "languageCode": "en",
                "regionCode": "IN",
                "maxResultCount": 5,
            },
            headers={
                "X-Goog-Api-Key": config.GOOGLE_MAPS_API_KEY,
                "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location",
            },
            timeout=8,
        )
        if res.status_code != 200:
            logger.warning("Google Places search failed with HTTP %s: %s", res.status_code, res.text[:500])
            raise HTTPException(
                status_code=502,
                detail="Google Places could not search right now. Check that Places API (New) and billing are enabled for the Google key.",
            )

        candidates = []
        for place in res.json().get("places", []):
            location = place.get("location") or {}
            if location.get("latitude") is None or location.get("longitude") is None:
                continue
            candidates.append({
                "name": (place.get("displayName") or {}).get("text") or "Unnamed place",
                "address": place.get("formattedAddress", ""),
                "lat": float(location["latitude"]),
                "lon": float(location["longitude"]),
                "place_id": place.get("id"),
                "source": "Google Places",
            })
        return candidates
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Google Places search request failed: %s", e)
        raise HTTPException(status_code=502, detail="Could not reach Google Places. Please try the search again.") from e


@router.post("/api/dropoff/confirm")
def confirm_dropoff(data: dict = Body(...), user: dict = Depends(require_authenticated_user)):
    name = str(data.get("name") or "").strip()
    address = str(data.get("address") or "").strip()
    lat = data.get("lat")
    lon = data.get("lon")
    confirmed_by = str(user.get("email") or "Supervisor")

    if lat is None or lon is None:
        return JSONResponse({"error": "Latitude and Longitude are required."}, status_code=400)

    try:
        confirmed_data = {
            "supervisor_id": _dropoff_file(user["id"])[1],
            "name": name,
            "address": address,
            "lat": float(lat),
            "lon": float(lon),
            "confirmed": True,
            "confirmed_by": confirmed_by,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if not is_within_pune_geofence(confirmed_data["lat"], confirmed_data["lon"]):
            return JSONResponse({"error": "Choose a drop-off inside the configured service area."}, status_code=400)
        dropoff_file, _safe_user_id = _dropoff_file(user["id"])
        dropoff_file.parent.mkdir(parents=True, exist_ok=True)
        with open(dropoff_file, "w", encoding="utf-8") as f:
            json.dump(confirmed_data, f, indent=2)
    except (TypeError, ValueError) as e:
        return JSONResponse({"error": f"Invalid latitude or longitude: {e}"}, status_code=400)
    except Exception as e:
        logger.error(f"Error saving supervisor drop-off location: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

    return {
        "status": "SUCCESS",
        "message": "Drop-off facility confirmed.",
        "dropoff": confirmed_data,
    }
