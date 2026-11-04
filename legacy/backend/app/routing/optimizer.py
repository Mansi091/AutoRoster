"""Capacitated Vehicle Routing Problem with Time Windows (CVRPTW) Optimizer.

THE ONE ALGORITHM: All route optimization and mathematical vehicle assignment
logic lives exclusively in this module.
"""

try:
    import google.protobuf.runtime_version
    google.protobuf.runtime_version.ValidateProtobufRuntimeVersion = lambda *args, **kwargs: None
except (ImportError, AttributeError):
    pass

from typing import Dict, List, Optional, Any
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from ..core.config import config
from ..core.logging import logger

def optimize_routes(
    time_matrix: List[List[int]],
    demands: List[int],
    stop_names: List[str],
    service_minutes: List[int],
    num_buses: int,
    earliest_departure: int,
    latest_departure: int,
    shift_start: int,
    max_route_minutes: int = 60,
    arrival_buffer: int = 10,
    bus_capacity: int = 32,
    time_limit_seconds: float = 1.5,
) -> Dict[str, Any]:
    """Optimizes bus assignment and stop ordering using Google OR-Tools CVRPTW.

    Constraints enforced:
    1. Vehicle capacity <= bus_capacity (32 seats).
    2. Total vehicle route duration span <= max_route_minutes (hard 60 min target).
    3. Factory arrival window before (shift_start - arrival_buffer).
    4. Vehicle fixed cost to minimize fleet size.
    """
    n = len(time_matrix)
    if n < 2 or len(demands) != n or len(stop_names) != n or len(service_minutes) != n:
        return {
            "status": "INVALID_INPUT",
            "message": f"Mismatched input lengths: matrix={n}, demands={len(demands)}, stops={len(stop_names)}"
        }

    depot_index = 0
    destination_index = n - 1

    # Verify individual stop demand does not exceed bus capacity
    for idx, d in enumerate(demands):
        if d > bus_capacity:
            return {
                "status": "DEMAND_EXCEEDS_CAPACITY",
                "message": f"Stop '{stop_names[idx]}' has {d} passengers, exceeding bus capacity {bus_capacity}."
            }

    latest_arrival = shift_start - arrival_buffer
    starts = [depot_index] * num_buses
    ends = [destination_index] * num_buses

    manager = pywrapcp.RoutingIndexManager(n, num_buses, starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    # 1. Travel Time Callback
    def time_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        dwell = service_minutes[from_node] if from_node != depot_index else 0
        return int(time_matrix[from_node][to_node] + dwell)

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # 2. Vehicle Capacity Dimension
    def demand_callback(from_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        return int(demands[from_node])

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,  # null capacity slack
        [bus_capacity] * num_buses,
        True,  # start cumul to zero
        "Capacity",
    )

    # 3. Time Windows Dimension & Strict Commute Span Constraint
    horizon_minutes = 24 * 60
    routing.AddDimension(
        transit_callback_index,
        horizon_minutes,
        horizon_minutes,
        False,
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")
    for vehicle_id in range(num_buses):
        depot_start_index = routing.Start(vehicle_id)
        time_dimension.CumulVar(depot_start_index).SetRange(int(earliest_departure), int(latest_arrival))

        dest_end_index = routing.End(vehicle_id)
        time_dimension.CumulVar(dest_end_index).SetRange(int(earliest_departure), int(latest_arrival))

        # Enforce maximum commute time span upper bound
        if max_route_minutes and max_route_minutes > 0:
            time_dimension.SetSpanUpperBoundForVehicle(int(max_route_minutes), vehicle_id)

    # Per-passenger ride limit: no passenger may board earlier than (latest_arrival - max_route_minutes)
    min_board_time = max(earliest_departure, latest_arrival - max_route_minutes)
    for stop_node in range(1, n - 1):
        index = manager.NodeToIndex(stop_node)
        time_dimension.CumulVar(index).SetRange(int(min_board_time), int(latest_arrival))

    # Minimize total vehicles by penalizing unused vehicle activation
    for vehicle_id in range(num_buses):
        routing.SetFixedCostOfVehicle(config.FLEET.fixed_vehicle_cost, vehicle_id)

    # Search parameters
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    tot_sec = float(time_limit_seconds)
    search_parameters.time_limit.seconds = int(tot_sec)
    search_parameters.time_limit.nanos = int((tot_sec - int(tot_sec)) * 1e9)

    solution = routing.SolveWithParameters(search_parameters)
    if not solution:
        return {
            "status": "NO_FEASIBLE_SOLUTION",
            "message": f"Infeasible routing solution under max_route_minutes={max_route_minutes} with {num_buses} buses."
        }

    # Format solution
    capacity_dimension = routing.GetDimensionOrDie("Capacity")
    routes = []

    for vehicle_id in range(num_buses):
        index = routing.Start(vehicle_id)
        # Skip empty vehicles
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue

        route_stops = []
        route_demand = 0

        while not routing.IsEnd(index):
            node_idx = manager.IndexToNode(index)
            time_var = time_dimension.CumulVar(index)
            cap_var = capacity_dimension.CumulVar(index)
            time_val = solution.Min(time_var)
            load_val = solution.Value(cap_var)

            hrs, mins = divmod(time_val, 60)
            time_clock = f"{int(hrs) % 24:02d}:{int(mins):02d}"

            route_stops.append({
                "node_index": node_idx,
                "stop_name": stop_names[node_idx],
                "time_minutes": time_val,
                "time_clock": time_clock,
                "passengers_boarding": demands[node_idx],
                "bus_load": load_val,
            })
            route_demand += demands[node_idx]
            index = solution.Value(routing.NextVar(index))

        # Destination node
        dest_node_idx = manager.IndexToNode(index)
        dest_time_var = time_dimension.CumulVar(index)
        dest_time = solution.Min(dest_time_var)
        d_hrs, d_mins = divmod(dest_time, 60)
        dest_clock = f"{int(d_hrs) % 24:02d}:{int(d_mins):02d}"

        route_stops.append({
            "node_index": dest_node_idx,
            "stop_name": stop_names[dest_node_idx],
            "time_minutes": dest_time,
            "time_clock": dest_clock,
            "passengers_boarding": 0,
            "bus_load": route_demand,
        })

        # Calculate duration from first real pickup stop to destination
        first_pickup_time = route_stops[1]["time_minutes"] if len(route_stops) > 2 else route_stops[0]["time_minutes"]
        duration = dest_time - first_pickup_time

        routes.append({
            "bus_id": f"Bus {vehicle_id + 1}",
            "vehicle_index": vehicle_id,
            "passengers_assigned": route_demand,
            "capacity": bus_capacity,
            "departure_time": route_stops[1]["time_clock"] if len(route_stops) > 2 else route_stops[0]["time_clock"],
            "arrival_time": dest_clock,
            "route_duration_minutes": duration,
            "total_stops": len(route_stops) - 2,
            "stops": route_stops,
        })

    return {
        "status": "OPTIMAL_OR_FEASIBLE",
        "total_buses_used": len(routes),
        "routes": routes,
    }
