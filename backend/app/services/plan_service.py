"""Transit planning and shift optimization workflow service."""

from typing import List, Dict, Any, Optional
from ..core.config import config
from ..core.logging import logger
from ..utils.geo import haversine_km, get_radial_corridor
from ..etl.transform.clean_employees import clean_employee_records
from ..etl.transform.coordinate_resolver import resolve_pickup_coordinates
from ..etl.transform.aggregate_stops import aggregate_employee_records
from ..etl.transform.road_matrix import build_corridor_road_matrix
from ..routing.optimizer import optimize_routes
from ..routing.schedule import build_route_schedule
from ..routing.validate import validate_and_enforce_commute_limits

class PlanService:
    def plan_shift(
        self,
        raw_employees: List[Dict[str, Any]],
        shift_filter: Optional[str] = None,
        bus_capacity: Optional[int] = None,
        destination: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Orchestrates end-to-end transit planning for employee shift rosters."""
        cap = bus_capacity or config.FLEET.standard_bus_capacity
        dest = destination or {}
        if dest.get("lat") is None or dest.get("lon") is None:
            raise ValueError("Choose and confirm a drop-off facility before planning routes.")

        # 1. Employee attribute normalization
        cleaned_employees = clean_employee_records(raw_employees)
        if shift_filter and shift_filter.upper() != "ALL":
            cleaned_employees = [e for e in cleaned_employees if e["shift"] == shift_filter.upper()]

        # 2. Geocoding resolution for any unverified stops
        unique_pickups = list(set(e["pickup_point"] for e in cleaned_employees if e.get("location_status") != "VERIFIED"))
        if unique_pickups:
            resolved_dict = resolve_pickup_coordinates(unique_pickups, facility=dest)
            for emp in cleaned_employees:
                p = emp["pickup_point"]
                if p in resolved_dict:
                    res = resolved_dict[p]
                    if emp.get("location_status") != "VERIFIED":
                        emp["latitude"] = res["latitude"]
                        emp["longitude"] = res["longitude"]
                        emp["location_status"] = res["location_status"]
                        emp["resolution_reason"] = res["resolution_reason"]

        # 3. Stop aggregation into physical graph nodes
        stops, unverified = aggregate_employee_records(cleaned_employees, bus_capacity=cap, facility=dest)
        if not stops:
            return {
                "status": "NO_STOPS_TO_ROUTE",
                "routes": [],
                "employees": cleaned_employees,
                "unverified_employees": unverified,
                "total_buses": 0,
            }

        # 4. Partition stops by shift and highway corridors
        shifts_present = sorted(list(set(e["shift"] for e in cleaned_employees if e.get("location_status") == "VERIFIED")))
        all_routes = []
        bus_counter = {}

        dest_node = {
            "stop_name": dest.get("name") or "Drop-off Facility",
            "lat": dest["lat"],
            "lon": dest["lon"],
            "demand": 0,
            "service_minutes": 0,
        }

        for sh in shifts_present:
            sh_stops = [s for s in stops if any(e.get("shift") == sh for e in s.get("employees", []))]
            if not sh_stops:
                continue

            earliest_dep, latest_dep, shift_start = config.SHIFT_WINDOWS.get(sh, (330, 400, 410))
            earliest_allowed_dep = shift_start - int(config.COMMUTE.target_commute_minutes) - 10

            corridor_groups: Dict[str, List[Dict[str, Any]]] = {}
            for s in sh_stops:
                corridor_groups.setdefault(s["corridor"], []).append(s)

            for corridor_name, c_stops in corridor_groups.items():
                depot_node = {
                    "stop_name": f"Depot ({corridor_name})",
                    "lat": dest["lat"],
                    "lon": dest["lon"],
                    "demand": 0,
                    "service_minutes": 0,
                }

                all_nodes, time_matrix, dist_matrix = build_corridor_road_matrix(depot_node, c_stops, dest_node)
                total_pax = sum(s["demand"] for s in c_stops)
                min_buses_needed = max(1, -(-total_pax // cap))
                max_buses_to_try = min(len(c_stops), max(min_buses_needed + 2, 4))

                corridor_solved_routes = []
                for nb in range(min_buses_needed, max_buses_to_try + 1):
                    res = optimize_routes(
                        time_matrix=time_matrix,
                        demands=[n["demand"] for n in all_nodes],
                        stop_names=[n["stop_name"] for n in all_nodes],
                        service_minutes=[n["service_minutes"] for n in all_nodes],
                        num_buses=nb,
                        earliest_departure=earliest_allowed_dep,
                        latest_departure=latest_dep,
                        shift_start=shift_start,
                        max_route_minutes=int(config.COMMUTE.target_commute_minutes),
                        arrival_buffer=config.COMMUTE.arrival_buffer_minutes,
                        bus_capacity=cap,
                        time_limit_seconds=1.2,
                    )
                    if res.get("status") in ("OPTIMAL_OR_FEASIBLE",) and res.get("routes"):
                        corridor_solved_routes = res["routes"]
                        break

                # Fallback sequential chunking if OR-Tools cannot find feasible route under 60 min
                if not corridor_solved_routes:
                    ordered_c = sorted(c_stops, key=lambda s: haversine_km(s["lat"], s["lon"], dest["lat"], dest["lon"]), reverse=True)
                    chunks = []
                    curr_ch = []
                    curr_dem = 0
                    curr_est_km = 0.0

                    for st in ordered_c:
                        dem = st["demand"]
                        dist_to_dest = haversine_km(st["lat"], st["lon"], dest["lat"], dest["lon"])
                        if not curr_ch:
                            hop = 0.0
                            added_km = dist_to_dest
                        else:
                            last_st = curr_ch[-1]
                            hop = haversine_km(last_st["lat"], last_st["lon"], st["lat"], st["lon"])
                            added_km = hop

                        test_km = curr_est_km + added_km
                        test_road_km = test_km * config.COMMUTE.pune_road_curvature_factor
                        test_mins = (test_road_km / config.COMMUTE.transit_speed_kmh) * 60.0 + (len(curr_ch) + 1) * config.COMMUTE.dwell_minutes_per_stop

                        need_split = False
                        if curr_ch:
                            if curr_dem + dem > (cap - 2):
                                need_split = True
                            elif test_mins > 56.0:
                                need_split = True
                            elif hop > 12.0:
                                need_split = True
                            elif test_road_km > 38.0:
                                need_split = True

                        if need_split:
                            chunks.append(curr_ch)
                            curr_ch = []
                            curr_dem = 0
                            curr_est_km = dist_to_dest

                        curr_ch.append(st)
                        curr_dem += dem
                        curr_est_km = dist_to_dest if len(curr_ch) == 1 else curr_est_km + hop

                    if curr_ch:
                        chunks.append(curr_ch)

                    for ch in chunks:
                        sched_stops, tot_km, tot_min = build_route_schedule(ch, dest_node)
                        bus_counter.setdefault(sh, 0)
                        bus_counter[sh] += 1
                        all_routes.append({
                            "bus_id": f"Bus {sh}-{bus_counter[sh]}",
                            "shift": f"Shift {sh}",
                            "corridor": corridor_name,
                            "passengers_assigned": sum(s["passengers_boarding"] for s in sched_stops),
                            "capacity": cap,
                            "seats_available": max(0, cap - sum(s["passengers_boarding"] for s in sched_stops)),
                            "total_stops": len(sched_stops),
                            "total_km": tot_km,
                            "road_km": round(tot_km * config.COMMUTE.pune_road_curvature_factor, 1),
                            "total_minutes": tot_min,
                            "longest_ride_minutes": tot_min,
                            "ride_time_status": "ON_TARGET" if tot_min <= 60.0 else "WITHIN_ALLOWANCE",
                            "destination_name": dest.get("name") or "Drop-off Facility",
                            "stops": sched_stops,
                        })
                else:
                    for r in corridor_solved_routes:
                        raw_sub_stops = [
                            c_stops[s["node_index"] - 1]
                            for s in r["stops"]
                            if 1 <= s["node_index"] <= len(c_stops)
                        ]
                        if not raw_sub_stops:
                            continue
                        sched_stops, tot_km, tot_min = build_route_schedule(raw_sub_stops, dest_node)
                        bus_counter.setdefault(sh, 0)
                        bus_counter[sh] += 1
                        all_routes.append({
                            "bus_id": f"Bus {sh}-{bus_counter[sh]}",
                            "shift": f"Shift {sh}",
                            "corridor": corridor_name,
                            "passengers_assigned": sum(s["passengers_boarding"] for s in sched_stops),
                            "capacity": cap,
                            "seats_available": max(0, cap - sum(s["passengers_boarding"] for s in sched_stops)),
                            "total_stops": len(sched_stops),
                            "total_km": tot_km,
                            "road_km": round(tot_km * config.COMMUTE.pune_road_curvature_factor, 1),
                            "total_minutes": tot_min,
                            "longest_ride_minutes": tot_min,
                            "ride_time_status": "ON_TARGET" if tot_min <= 60.0 else "WITHIN_ALLOWANCE",
                            "destination_name": dest.get("name") or "Drop-off Facility",
                            "stops": sched_stops,
                        })

        # 5. Strictly enforce maximum commute duration <= 60 min (max 70 min)
        final_validated_routes = validate_and_enforce_commute_limits(all_routes, dest_node)

        # 6. Renumber buses neatly and map individual passenger assignments
        all_assigned_employees = []
        b_idx_map = {}
        for r in final_validated_routes:
            sh = r["shift"].replace("Shift ", "")
            b_idx_map.setdefault(sh, 0)
            b_idx_map[sh] += 1
            new_bus_id = f"Bus {sh}-{b_idx_map[sh]}"
            r["bus_id"] = new_bus_id

            for st in r["stops"]:
                for emp in st.get("employees", []):
                    all_assigned_employees.append({
                        "sr_no": len(all_assigned_employees) + 1,
                        "employee_id": emp.get("employee_id", ""),
                        "employee_name": emp.get("employee_name", ""),
                        "grade": emp.get("grade", ""),
                        "shift": emp.get("shift", sh),
                        "corridor": r["corridor"],
                        "pickup_point": emp.get("pickup_point", ""),
                        "assigned_bus": new_bus_id,
                        "mobile": emp.get("mobile", ""),
                        "lat": st["lat"],
                        "lon": st["lon"],
                        "seq": st["seq"],
                    })

        return {
            "status": "OK",
            "routes": final_validated_routes,
            "employees": all_assigned_employees,
            "routed_employees": sum(r["passengers_assigned"] for r in final_validated_routes),
            "unverified_employees": unverified,
            "total_buses": len(final_validated_routes),
            "total_stops": sum(len(r["stops"]) for r in final_validated_routes),
            "destination": dest,
        }

plan_service = PlanService()
