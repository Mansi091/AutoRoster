"""Geocoding candidate validation, scoring, and service radius classification."""

from typing import Dict, Any, Optional, Tuple
from ...core.config import config
from ...utils.geo import haversine_km, is_within_pune_geofence

def evaluate_coordinate_candidate(
    lat: Optional[float],
    lon: Optional[float],
    raw_confidence: float = 1.0,
    facility: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Optional[str]]:
    """Evaluates candidate GPS coordinates against Pune geofence and service radius.

    Returns:
        (location_status, resolution_reason)
        Status can be: 'VERIFIED', 'OUTSIDE_SERVICE_RADIUS', or 'LOCATION_UNVERIFIED'
    """
    if lat is None or lon is None:
        return "LOCATION_UNVERIFIED", "Missing or null GPS coordinates"

    if not facility or facility.get("lat") is None or facility.get("lon") is None:
        return "LOCATION_UNVERIFIED", "Choose a drop-off facility before verifying pickup distance."

    if not is_within_pune_geofence(lat, lon):
        return "LOCATION_UNVERIFIED", f"Coordinate [{lat}, {lon}] is outside Pune service boundary."

    dist_to_facility = haversine_km(lat, lon, float(facility["lat"]), float(facility["lon"]))
    if dist_to_facility > config.FACILITY.max_service_radius_km:
        return (
            "OUTSIDE_SERVICE_RADIUS",
            f"Pickup is {dist_to_facility:.1f} km from facility (exceeds {config.FACILITY.max_service_radius_km} km practical radius).",
        )

    if raw_confidence < 0.5:
        return "LOCATION_UNVERIFIED", f"Low geocoding confidence score ({raw_confidence:.2f})."

    return "VERIFIED", f"Verified coordinate ({dist_to_facility:.1f} km from plant)."
