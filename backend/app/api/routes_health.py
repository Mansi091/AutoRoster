"""Health and operational diagnostic endpoints."""

from fastapi import APIRouter

from ..core.config import config

router = APIRouter()


@router.get("/api/health")
def health_check():
    return {
        "status": "HEALTHY",
        "service": "AutoRoster Transit Engine",
        "facility": None,
        "max_commute_target_minutes": config.COMMUTE.target_commute_minutes,
        "hard_max_commute_minutes": config.COMMUTE.hard_max_commute_minutes,
        "bus_capacity": config.FLEET.standard_bus_capacity,
        "keys_configured": {
            "google_maps": bool(config.GOOGLE_MAPS_API_KEY),
            "locationiq": bool(config.LOCATIONIQ_API_KEY),
            "geoapify": bool(config.GEOAPIFY_API_KEY),
            "gemini": bool(config.GEMINI_API_KEY),
        },
    }
