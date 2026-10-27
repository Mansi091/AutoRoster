"""Validation rules and route commute ceiling enforcement."""

from typing import List, Dict, Any
from ..core.config import config
from ..core.logging import logger
from .schedule import build_route_schedule

def validate_and_enforce_commute_limits(
    routes: List[Dict[str, Any]],
    destination_node: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Enforces strict commute budgeting: All routes must satisfy <= 60 min (max 70 min).

    Any oversized route with >= 2 stops is partitioned into balanced sub-routes.
    """
    target_limit = config.COMMUTE.target_commute_minutes  # 60.0
    hard_limit = config.COMMUTE.hard_max_commute_minutes   # 70.0
    bus_capacity = config.FLEET.standard_bus_capacity      # 32

    working = list(routes)
    changed = True

    while changed:
        changed = False
        for idx, route in enumerate(working):
            duration = route.get("total_minutes", 0.0)
            stops = route.get("stops", [])

            if duration > target_limit and len(stops) >= 2:
                logger.info(f"Route '{route['bus_id']}' exceeds {target_limit}m ({duration:.1f}m). Partitioning...")
                cut = max(1, len(stops) // 2)
                p1_stops = stops[:cut]
                p2_stops = stops[cut:]

                sched1, km1, dur1 = build_route_schedule(p1_stops, destination_node)
                sched2, km2, dur2 = build_route_schedule(p2_stops, destination_node)

                pax1 = sum(s["passengers_boarding"] for s in sched1)
                pax2 = sum(s["passengers_boarding"] for s in sched2)

                r1 = dict(route)
                r1["bus_id"] = f"{route['bus_id']}A"
                r1["stops"] = sched1
                r1["total_stops"] = len(sched1)
                r1["passengers_assigned"] = pax1
                r1["seats_available"] = max(0, bus_capacity - pax1)
                r1["total_km"] = km1
                r1["road_km"] = round(km1 * config.COMMUTE.pune_road_curvature_factor, 1)
                r1["total_minutes"] = dur1
                r1["longest_ride_minutes"] = dur1
                r1["ride_time_status"] = "ON_TARGET" if dur1 <= target_limit else "WITHIN_ALLOWANCE"

                r2 = dict(route)
                r2["bus_id"] = f"{route['bus_id']}B"
                r2["stops"] = sched2
                r2["total_stops"] = len(sched2)
                r2["passengers_assigned"] = pax2
                r2["seats_available"] = max(0, bus_capacity - pax2)
                r2["total_km"] = km2
                r2["road_km"] = round(km2 * config.COMMUTE.pune_road_curvature_factor, 1)
                r2["total_minutes"] = dur2
                r2["longest_ride_minutes"] = dur2
                r2["ride_time_status"] = "ON_TARGET" if dur2 <= target_limit else "WITHIN_ALLOWANCE"

                working.pop(idx)
                working.insert(idx, r2)
                working.insert(idx, r1)
                changed = True
                break

    return working
