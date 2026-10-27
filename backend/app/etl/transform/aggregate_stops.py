"""Physical stop aggregation and capacity chunking."""

import math
from typing import List, Dict, Tuple, Any
from ...core.config import config
from ...utils.geo import haversine_km, get_radial_corridor
from ...db.models import PhysicalStop

def aggregate_employee_records(
    employees: List[Dict[str, Any]],
    bus_capacity: int = 32,
    facility: Dict[str, Any] | None = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Aggregates verified employee records into unique physical pickup stops.

    Splits any stop with demand > bus_capacity into balanced chunks.

    Returns:
        (aggregated_stops, unverified_employees)
    """
    stops_grouped: Dict[str, Dict[str, Any]] = {}
    unverified: List[Dict[str, Any]] = []

    for emp in employees:
        lat = emp.get("latitude")
        lon = emp.get("longitude")
        status = emp.get("location_status", "UNVERIFIED")

        if lat is None or lon is None or status != "VERIFIED":
            unverified.append(emp)
            continue

        # Coordinate round key (approx 10-meter cluster merging)
        coord_key = f"{round(float(lat), 4)}_{round(float(lon), 4)}"
        stop_name = emp.get("pickup_point") or "Pickup Point"

        if coord_key not in stops_grouped:
            stops_grouped[coord_key] = {
                "stop_name": stop_name,
                "lat": float(lat),
                "lon": float(lon),
                "employees": [],
            }
        stops_grouped[coord_key]["employees"].append(emp)

    aggregated_stops: List[Dict[str, Any]] = []
    if not facility or facility.get("lat") is None or facility.get("lon") is None:
        raise ValueError("A confirmed drop-off facility is required to group pickup stops.")
    facility_lat, facility_lon = float(facility["lat"]), float(facility["lon"])

    for key, data in stops_grouped.items():
        total_pax = len(data["employees"])
        lat, lon = data["lat"], data["lon"]
        corridor = get_radial_corridor(lat, lon, facility_lat, facility_lon)
        dist_to_dest = haversine_km(lat, lon, facility_lat, facility_lon)

        if total_pax <= bus_capacity:
            service_mins = max(1, math.ceil(total_pax * 0.25))
            aggregated_stops.append({
                "stop_name": data["stop_name"],
                "lat": lat,
                "lon": lon,
                "demand": total_pax,
                "service_minutes": service_mins,
                "corridor": corridor,
                "distance_to_destination_km": round(dist_to_dest, 1),
                "employees": data["employees"],
            })
        else:
            # Split into chunks of size <= bus_capacity
            emps = data["employees"]
            for chunk_idx, i in enumerate(range(0, total_pax, bus_capacity), 1):
                chunk = emps[i:i + bus_capacity]
                service_mins = max(1, math.ceil(len(chunk) * 0.25))
                aggregated_stops.append({
                    "stop_name": f"{data['stop_name']} (Group {chunk_idx})",
                    "lat": lat,
                    "lon": lon,
                    "demand": len(chunk),
                    "service_minutes": service_mins,
                    "corridor": corridor,
                    "distance_to_destination_km": round(dist_to_dest, 1),
                    "employees": chunk,
                })

    return aggregated_stops, unverified
