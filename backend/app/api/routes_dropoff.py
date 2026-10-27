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
from ..core.logging import logger
from ..utils.geo import is_within_pune_geofence

router = APIRouter()
DROPOFF_DIR = config.DATA_DIR / "dropoff_locations"
DEFAULT_DROPOFF = {
    "name": "JCB India Ltd, Talegaon",
    "address": "Talegaon MIDC, Vadgaon, Pune, Maharashtra 410507",
    "lat": 18.7765871,
    "lon": 73.6947478,
    "confirmed": True,
    "confirmed_by": "System Default",
    "updated_at": None,
}

PLANT_PRESETS = [
    {
        "name": "JCB India Ltd, Talegaon",
        "address": "Talegaon MIDC, Vadgaon, Pune, Maharashtra 410507",
        "lat": 18.7765871,
        "lon": 73.6947478,
        "source": "Verified Plant Master",
    },
    {
        "name": "JCB Pune Plant (Talegaon Industrial Area)",
        "address": "Plot No. A-1, Talegaon Industrial Area, Pune",
        "lat": 18.7765871,
        "lon": 73.6947478,
        "source": "Verified Plant Master",
    },
]


def _dropoff_file(user_id: str):
    safe_user_id = re.sub(r"[^A-Za-z0-9_-]", "_", user_id or "default_supervisor")
    return DROPOFF_DIR / f"{safe_user_id}.json", safe_user_id


def get_current_dropoff_data(user_id: str):
    dropoff_file, safe_user_id = _dropoff_file(user_id)
    if dropoff_file.exists():
        try:
            with open(dropoff_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if (data.get("confirmed")
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
            "address": f"Google Maps pin at {lat:.6f}, {lon:.6f}",
            "lat": lat,
            "lon": lon,
            "source": "Google Maps link",
        }], name

    return None, name


@router.get("/api/dropoff/current")
def get_current_dropoff():
    return DEFAULT_DROPOFF


@router.get("/api/dropoff/search")
def search_dropoff(q: str = Query(default="")):
    query = q.strip()
    q_lower = query.lower()

    if not query:
        return PLANT_PRESETS

    matched = [
        p for p in PLANT_PRESETS
        if q_lower in p["name"].lower() or q_lower in p["address"].lower() or any(k in q_lower for k in ["jcb", "talegaon", "plant", "pune", "midc", "factory"])
    ]
    if matched:
        return matched

    map_candidates, map_place_name = _google_maps_location(query)
    if map_candidates is not None:
        return map_candidates

    search_query = map_place_name or query
    if not config.GOOGLE_MAPS_API_KEY:
        return PLANT_PRESETS

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
        if res.status_code == 200:
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
            if candidates:
                return candidates
        return PLANT_PRESETS
    except Exception as e:
        logger.warning("Places search error: %s", e)
        return PLANT_PRESETS


@router.post("/api/dropoff/confirm")
def confirm_dropoff(data: dict = Body(...)):
    name = str(data.get("name") or "").strip()
    address = str(data.get("address") or "").strip()
    lat = data.get("lat")
    lon = data.get("lon")
    confirmed_by = str(data.get("confirmed_by") or "Supervisor (Web Dashboard)")

    if lat is None or lon is None:
        return JSONResponse({"error": "Latitude and Longitude are required."}, status_code=400)

    try:
        confirmed_data = {
            "supervisor_id": "supervisor",
            "name": name or "JCB India Ltd, Talegaon",
            "address": address or "Talegaon MIDC, Vadgaon, Pune, Maharashtra 410507",
            "lat": float(lat),
            "lon": float(lon),
            "confirmed": True,
            "confirmed_by": confirmed_by,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        dropoff_file, _ = _dropoff_file("supervisor")
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
