"""Central configuration module for AutoRoster.

SINGLE SOURCE OF TRUTH: All operational rules, shift timings, plant coordinates,
speed models, vehicle limits, and environmental parameters are defined here.
No other module may define or hardcode these values.
"""

import os
from pathlib import Path
from typing import Dict, Tuple
from pydantic import BaseModel
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(ROOT_DIR / ".env")
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = ROOT_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
SEED_DIR = DATA_DIR / "seed"
OUTPUT_DIR = ROOT_DIR / "output"

# Ensure directories exist
for directory in (CACHE_DIR, SEED_DIR, OUTPUT_DIR):
    directory.mkdir(parents=True, exist_ok=True)


class FacilityConfig(BaseModel):
    # Destination coordinates are always supplied by the supervisor's
    # confirmed selection, never from a global default.
    name: str = ""
    lat: float | None = None
    lon: float | None = None
    max_service_radius_km: float = float(os.getenv("MAX_SERVICE_RADIUS_KM", "50"))


class FleetConfig(BaseModel):
    standard_bus_capacity: int = int(os.getenv("BUS_CAPACITY", "32"))
    max_safe_cluster_demand: int = int(os.getenv("MAX_SAFE_CLUSTER_DEMAND", "30"))
    fixed_vehicle_cost: int = 1000  # Fleet minimization penalty


class CommuteConfig(BaseModel):
    target_commute_minutes: float = float(os.getenv("TARGET_RIDE_TIME_MINUTES", "60"))
    hard_max_commute_minutes: float = float(os.getenv("MAX_RIDE_TIME_MINUTES", "70"))
    arrival_buffer_minutes: int = int(os.getenv("ARRIVAL_BUFFER_MINUTES", "10"))
    dwell_minutes_per_stop: float = float(os.getenv("STOP_DWELL_MINUTES", "1.5"))
    dwell_seconds_per_passenger: float = float(os.getenv("BOARDING_SECONDS_PER_PASSENGER", "0"))
    pune_road_curvature_factor: float = 1.45
    transit_speed_kmh: float = 28.0
    highway_express_speed_kmh: float = 45.0


class GeofenceConfig(BaseModel):
    lat_min: float = float(os.getenv("SERVICE_AREA_LAT_MIN", "17.9"))
    lat_max: float = float(os.getenv("SERVICE_AREA_LAT_MAX", "19.2"))
    lon_min: float = float(os.getenv("SERVICE_AREA_LON_MIN", "73.1"))
    lon_max: float = float(os.getenv("SERVICE_AREA_LON_MAX", "74.6"))


def _time_to_minutes(value: str, fallback: str) -> int:
    try:
        hours, minutes = (int(part) for part in value.split(":", 1))
        if 0 <= hours < 24 and 0 <= minutes < 60:
            return hours * 60 + minutes
    except (AttributeError, TypeError, ValueError):
        pass
    hours, minutes = (int(part) for part in fallback.split(":", 1))
    return hours * 60 + minutes


class AppConfig:
    # Directories
    ROOT_DIR: Path = ROOT_DIR
    BACKEND_DIR: Path = BACKEND_DIR
    DATA_DIR: Path = DATA_DIR
    CACHE_DIR: Path = CACHE_DIR
    SEED_DIR: Path = SEED_DIR
    OUTPUT_DIR: Path = OUTPUT_DIR

    # Comma-separated browser origins allowed to call the API.
    FRONTEND_ORIGINS: list[str] = [
        origin.strip().rstrip("/")
        for origin in os.getenv(
            "FRONTEND_ORIGINS",
            "*",
        ).split(",")
        if origin.strip()
    ]

    # Plant & Facility
    FACILITY: FacilityConfig = FacilityConfig()

    # Bus Fleet
    FLEET: FleetConfig = FleetConfig()

    # Commute Budgeting
    COMMUTE: CommuteConfig = CommuteConfig()

    # Geofence Bounding Box
    GEOFENCE: GeofenceConfig = GeofenceConfig()

    # The route planner works backward from these plant arrival deadlines.
    # Configure the roster-specific shift deadlines in the project .env.
    SHIFT_ARRIVAL_DEADLINES: Dict[str, int] = {
        "A": _time_to_minutes(os.getenv("SHIFT_A_ARRIVAL_DEADLINE", "06:40"), "06:40"),
        "B": _time_to_minutes(os.getenv("SHIFT_B_ARRIVAL_DEADLINE", "15:10"), "15:10"),
        "C": _time_to_minutes(os.getenv("SHIFT_C_ARRIVAL_DEADLINE", "23:35"), "23:35"),
    }

    # Retained only for the legacy single-shift scheduling helper.
    SHIFT_WINDOWS: Dict[str, Tuple[int, int, int]] = {
        "A": (330, 400, 410),
        "B": (810, 880, 890),
        "C": (1290, 1360, 1370),
    }

    # API Keys & Remote Endpoints
    LOCATIONIQ_API_KEY: str = os.getenv("LOCATIONIQ_API_KEY", "")
    GEOAPIFY_API_KEY: str = os.getenv("GEOAPIFY_API_KEY", "")
    GOOGLE_MAPS_API_KEY: str = os.getenv("GOOGLE_MAPS_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    OSRM_TABLE_URL: str = os.getenv("OSRM_TABLE_URL", "https://router.project-osrm.org/table/v1/driving")
    OSRM_ROUTE_URL: str = os.getenv("OSRM_ROUTE_URL", "https://router.project-osrm.org/route/v1/driving")
    ROUTE_SOLVER_TIME_LIMIT_SECONDS: int = max(
        1, min(300, int(os.getenv("ROUTE_SOLVER_TIME_LIMIT_SECONDS", "30")))
    )
    PICKUP_MERGE_RADIUS_METERS: float = float(os.getenv("PICKUP_MERGE_RADIUS_METERS", "400"))

    # File Paths
    MASTER_STOPS_FILE: Path = SEED_DIR / "stops_seed.json"
    GEOAPIFY_CACHE_FILE: Path = CACHE_DIR / "geoapify_cache.json"
    LLM_CACHE_FILE: Path = CACHE_DIR / "llm_corrections_cache.json"


config = AppConfig()
