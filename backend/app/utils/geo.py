"""Geographic and spatial utility calculations."""

import math
from typing import Optional, Tuple
from ..core.config import config

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes the great-circle distance between two GPS coordinates in kilometers."""
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return 0.0
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))

def is_within_pune_geofence(lat: Optional[float], lon: Optional[float]) -> bool:
    """Checks if a point falls strictly within the Pune Metropolitan transit bounding box."""
    if lat is None or lon is None:
        return False
    gf = config.GEOFENCE
    return gf.lat_min <= lat <= gf.lat_max and gf.lon_min <= lon <= gf.lon_max

def get_radial_corridor(lat: float, lon: float, dest_lat: Optional[float] = None, dest_lon: Optional[float] = None) -> str:
    """Classifies a coordinate into a highway transit corridor leading towards the plant."""
    if lat is None or lon is None:
        return "Local Vicinity"

    if dest_lat is None or dest_lon is None:
        return "Local Vicinity"
    f_lat = dest_lat
    f_lon = dest_lon

    d = haversine_km(lat, lon, f_lat, f_lon)
    if d <= 5.5:
        return "Local Vicinity"

    d_lon = math.radians(lon - f_lon)
    lat1 = math.radians(f_lat)
    lat2 = math.radians(lat)
    y = math.sin(d_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(d_lon)
    bearing = (math.degrees(math.atan2(y, x)) + 360) % 360

    if 45 <= bearing < 135:
        return "East Corridor"
    elif 135 <= bearing < 225:
        return "South Corridor"
    elif 225 <= bearing < 315:
        return "West Corridor"
    else:
        return "East Corridor"
