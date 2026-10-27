"""Capacitated Vehicle Routing Problem with Time Windows (CVRPTW) Optimizer.

Solves the dual problem simultaneously using Google OR-Tools:
1. Grouping: Which pickup stops are assigned to which bus (under a hard capacity limit, e.g., 32 seats).
2. Routing: In what exact sequence each bus visits its assigned stops and arrives at the company facility.

Node convention:
- 0: Bus Depot / Starting Point (all buses originate here)
- 1 ... n-2: Pickup stops (with positive demand and service/boarding times)
- n-1: Facility / Drop Point (all buses terminate here)
"""

try:
    import google.protobuf.runtime_version
    google.protobuf.runtime_version.ValidateProtobufRuntimeVersion = lambda *args, **kwargs: None
except (ImportError, AttributeError):
    pass

from typing import Dict, List, Optional, Any

from ortools.constraint_solver import pywrapcp, routing_enums_pb2


def optimize_routes(
    time_matrix: List[List[int]],
    demands: List[int],
    stop_names: List[str],
    service_minutes: List[int],
    num_buses: int,
    earliest_departure: int,
    latest_departure: int,
    shift_start: int,
    max_route_minutes: int = 50,
    arrival_buffer: int = 10,
    bus_capacity: int = 32,
    time_limit_seconds: int = 30,
) -> Dict[str, Any]:
    """Optimizes bus assignment and stop ordering using OR-Tools CVRPTW.

    Args:
        time_matrix: 2D list (n x n) of road travel times in integer minutes.
        demands: List of length n with passenger counts at each node.
        stop_names: List of length n with node/stop names.
        service_minutes: List of length n with boarding/dwell times in minutes.
        num_buses: Maximum number of buses available in the fleet.
        earliest_departure: Earliest time (minutes from midnight) a bus can depart depot.
        latest_departure: Latest time (minutes from midnight) a bus can depart depot.
        shift_start: Shift start time (minutes from midnight).
        max_route_minutes: Target maximum route duration from departure to facility arrival.
        arrival_buffer: Minutes buses must arrive before shift_start (e.g., 10 mins early).
        bus_capacity: Hard maximum seat limit per bus (default 32).
        time_limit_seconds: Maximum solver search time in seconds.

    Returns:
        Dict with status, routes, total_buses_used, and metric summaries.
    """
    n = len(time_matrix)
    if n < 2 or len(demands) != n or len(stop_names) != n or len(service_minutes) != n:
        return {
            "status": "INVALID_INPUT",
            "message": f"Mismatched input lengths: matrix={n}, demands={len(demands)}, stops={len(stop_names)}, service={len(service_minutes)}"
        }

    depot_index = 0
    destination_index = n - 1

    # Verify individual stop demand does not exceed bus capacity
    for idx, d in enumerate(demands):
        if d > bus_capacity:
            return {
                "status": "DEMAND_EXCEEDS_CAPACITY",
                "message": f"Stop '{stop_names[idx]}' has {d} passengers, exceeding bus capacity {bus_capacity}. Please split this stop."
            }

    # Latest arrival time at workplace
    latest_arrival = shift_start - arrival_buffer

    # Routing index manager:
    # Starts at depot (node 0) for all vehicles, ends at destination (node n-1)
    starts = [depot_index] * num_buses
    ends = [destination_index] * num_buses
    manager = pywrapcp.RoutingIndexManager(n, num_buses, starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    # 1. Travel Time Callback
    def time_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        # Travel time between nodes + boarding/service dwell time at departure stop
        dwell = service_minutes[from_node] if from_node != depot_index else 0
        return int(time_matrix[from_node][to_node] + dwell)

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # 2. Demand / Capacity Dimension
    def demand_callback(from_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        return int(demands[from_node])

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,  # null capacity slack
        [bus_capacity] * num_buses,  # vehicle maximum capacities
        True,  # start cumul to zero
        "Capacity"
    )

    # 3. Time Windows Dimension
    horizon_minutes = 24 * 60  # 1440 minutes in a day
    routing.AddDimension(
        transit_callback_index,
        horizon_minutes,  # allow waiting time if necessary
        horizon_minutes,  # maximum time per vehicle
        False,  # don't force start cumul to zero (start time is decision variable)
        "Time"
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    # Time window constraints:
    # Ensure buses arrive at the facility before latest_arrival (shift_start - buffer)
    # The departure time can be early enough to complete the route within max_route_minutes.
    calc_earliest_dep = min(earliest_departure, latest_arrival - max_route_minutes - 30)

    for vehicle_id in range(num_buses):
        depot_start_index = routing.Start(vehicle_id)
        time_dimension.CumulVar(depot_start_index).SetRange(
            int(calc_earliest_dep), int(latest_arrival)
        )

        # Facility arrival window
        dest_end_index = routing.End(vehicle_id)
        time_dimension.CumulVar(dest_end_index).SetRange(
            int(calc_earliest_dep), int(latest_arrival)
        )

        # Set route duration limit if max_route_minutes is specified
        if max_route_minutes and max_route_minutes > 0:
            time_dimension.SetSpanUpperBoundForVehicle(int(max_route_minutes), vehicle_id)

    # Intermediate pickup stops: must be visited between earliest possible departure and latest arrival
    for stop_node in range(1, n - 1):
        index = manager.NodeToIndex(stop_node)
        time_dimension.CumulVar(index).SetRange(
            int(calc_earliest_dep), int(latest_arrival)
        )

    # Minimize total vehicles used by adding fixed vehicle cost
    # This prevents using unnecessary empty buses
    for vehicle_id in range(num_buses):
        routing.SetFixedCostOfVehicle(1000, vehicle_id)

    # Search parameters
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    tot_sec = float(time_limit_seconds)
    search_parameters.time_limit.seconds = int(tot_sec)
    search_parameters.time_limit.nanos = int((tot_sec - int(tot_sec)) * 1e9)

    # Solve
    solution = routing.SolveWithParameters(search_parameters)

    if not solution:
        return {
            "status": "NO_FEASIBLE_SOLUTION",
            "message": (
                "Could not find a feasible routing solution that satisfies all constraints "
                f"(max_route_minutes={max_route_minutes}, bus_capacity={bus_capacity}, "
                f"latest_arrival={latest_arrival}). Try increasing the number of buses or max_route_minutes."
            )
        }

    # Format solution into readable result
    capacity_dimension = routing.GetDimensionOrDie("Capacity")
    routes_output = []
    total_travel_minutes = 0
    total_pax_serviced = 0

    for vehicle_id in range(num_buses):
        if not routing.IsVehicleUsed(solution, vehicle_id):
            continue

        index = routing.Start(vehicle_id)
        route_stops = []
        route_demands = []
        arrival_times = []
        cumulative_pax = 0

        start_time = solution.Value(time_dimension.CumulVar(index))

        while not routing.IsEnd(index):
            node = manager.IndexToNode(index)
            t_val = solution.Value(time_dimension.CumulVar(index))
            load = solution.Value(capacity_dimension.CumulVar(index))

            route_stops.append({
                "node_index": node,
                "stop_name": stop_names[node],
                "time_minutes": t_val,
                "time_clock": f"{t_val // 60:02d}:{t_val % 60:02d}",
                "passengers_boarding": demands[node],
                "bus_load": load
            })
            index = solution.Value(routing.NextVar(index))

        # Destination node
        dest_node = manager.IndexToNode(index)
        end_time = solution.Value(time_dimension.CumulVar(index))
        final_load = solution.Value(capacity_dimension.CumulVar(index))
        route_stops.append({
            "node_index": dest_node,
            "stop_name": stop_names[dest_node],
            "time_minutes": end_time,
            "time_clock": f"{end_time // 60:02d}:{end_time % 60:02d}",
            "passengers_boarding": 0,
            "bus_load": final_load
        })

        route_duration = end_time - start_time
        total_travel_minutes += route_duration
        total_pax_serviced += final_load

        routes_output.append({
            "bus_id": f"Bus {len(routes_output) + 1}",
            "vehicle_index": vehicle_id,
            "passengers_assigned": final_load,
            "capacity": bus_capacity,
            "departure_time": f"{start_time // 60:02d}:{start_time % 60:02d}",
            "arrival_time": f"{end_time // 60:02d}:{end_time % 60:02d}",
            "route_duration_minutes": route_duration,
            "total_stops": len(route_stops) - 2,  # excluding depot and final destination
            "stops": route_stops
        })

    return {
        "status": "OPTIMAL_OR_FEASIBLE",
        "total_buses_used": len(routes_output),
        "total_passengers": total_pax_serviced,
        "total_travel_minutes": total_travel_minutes,
        "routes": routes_output
    }


if __name__ == "__main__":
    # Self-test using the illustrative example dataset from user specification
    stop_names = [
        "Bus Depot",
        "Stop A",
        "Stop B",
        "Stop C",
        "Stop D",
        "Stop E",
        "Workplace Facility",
    ]
    demands = [0, 10, 8, 12, 7, 9, 0]
    service_minutes = [0, 2, 2, 3, 2, 2, 0]

    # Illustrative travel times (symmetric for testing)
    # 0=Depot, 1=A, 2=B, 3=C, 4=D, 5=E, 6=Facility
    time_matrix = [
        [0,  8, 12, 15, 20, 25, 30],  # Depot
        [8,  0,  5, 10, 15, 18, 22],  # Stop A
        [12, 5,  0,  8, 14, 16, 20],  # Stop B
        [15, 10, 8,  0,  7, 12, 16],  # Stop C
        [20, 15, 14, 7,  0,  6, 12],  # Stop D
        [25, 18, 16, 12, 6,  0, 10],  # Stop E
        [30, 22, 20, 16, 12, 10,  0],  # Workplace Facility
    ]

    print("Running initial CVRPTW optimizer test...")
    res = optimize_routes(
        time_matrix=time_matrix,
        demands=demands,
        stop_names=stop_names,
        service_minutes=service_minutes,
        num_buses=5,
        earliest_departure=330,  # 05:30
        latest_departure=350,    # 05:50
        shift_start=410,         # 06:50
        max_route_minutes=50,
        arrival_buffer=10,
        bus_capacity=32,
    )

    import pprint
    pprint.pprint(res)
