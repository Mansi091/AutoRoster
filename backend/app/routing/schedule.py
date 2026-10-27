"""Route stop sequencing and schedule construction."""

from typing import List, Dict, Any, Tuple
from ..utils.geo import haversine_km
from ..core.config import config

def build_route_schedule(
    raw_stops: List[Dict[str, Any]],
    destination_node: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], float, float]:
    """Computes stop-by-stop cumulative kilometers, passenger accumulations, and final duration.

    Returns:
        (scheduled_stops, total_route_km, duration_minutes)
    """
    scheduled = []
    cum_km = 0.0
    cum_pax = 0
    prev_lat = prev_lon = None

    for idx, stop in enumerate(raw_stops, 1):
        lat, lon = stop["lat"], stop["lon"]
        dist = 0.0
        if prev_lat is not None:
            dist = haversine_km(prev_lat, prev_lon, lat, lon)
        cum_km += dist
        cum_pax += stop.get("demand", len(stop.get("employees", [])))
        dist_to_dest = haversine_km(lat, lon, destination_node["lat"], destination_node["lon"])

        # Commute calculation for this individual pickup stop to destination
        road_km_to_dest = dist_to_dest * config.COMMUTE.pune_road_curvature_factor
        est_min = (road_km_to_dest / config.COMMUTE.transit_speed_kmh) * 60.0

        scheduled.append({
            "seq": idx,
            "stop_name": stop["stop_name"],
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "distance_from_prev_km": round(dist, 1),
            "cumulative_km": round(cum_km, 1),
            "passengers_boarding": stop.get("demand", len(stop.get("employees", []))),
            "cumulative_passengers": cum_pax,
            "distance_to_destination_km": round(dist_to_dest, 1),
            "ride_minutes_to_destination": round(est_min, 1),
            "ride_time_status": "ON_TARGET" if est_min <= config.COMMUTE.target_commute_minutes else "WITHIN_ALLOWANCE" if est_min <= config.COMMUTE.hard_max_commute_minutes else "EXCEEDS_LIMIT",
            "employees": stop.get("employees", []),
        })
        prev_lat, prev_lon = lat, lon

    final_leg_km = haversine_km(scheduled[-1]["lat"], scheduled[-1]["lon"], destination_node["lat"], destination_node["lon"])
    total_km = round(cum_km + final_leg_km, 1)

    # Total vehicle route duration with dwell time
    total_road_km = total_km * config.COMMUTE.pune_road_curvature_factor
    total_duration = round((total_road_km / config.COMMUTE.transit_speed_kmh) * 60.0 + len(scheduled) * config.COMMUTE.dwell_minutes_per_stop, 1)

    return scheduled, total_km, total_duration
