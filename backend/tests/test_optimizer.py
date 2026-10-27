"""Unit tests for the ONE OR-Tools optimizer algorithm."""

import unittest
from backend.app.routing.optimizer import optimize_routes

class TestOptimizer(unittest.TestCase):
    def test_single_bus_feasible_route(self):
        # 3 stops + Depot (0) + Dest (4)
        # 0 -> 1 -> 2 -> 3 -> 4
        time_matrix = [
            [0,  0,  0,  0,  0],   # Depot
            [15, 0,  10, 20, 25],  # Stop 1
            [20, 10, 0,  12, 18],  # Stop 2
            [25, 20, 12, 0,  10],  # Stop 3
            [30, 25, 18, 10, 0],   # Facility
        ]
        demands = [0, 5, 8, 4, 0]
        stop_names = ["Depot", "Stop 1", "Stop 2", "Stop 3", "JCB Plant"]
        service_mins = [0, 2, 2, 2, 0]

        res = optimize_routes(
            time_matrix=time_matrix,
            demands=demands,
            stop_names=stop_names,
            service_minutes=service_mins,
            num_buses=1,
            earliest_departure=330,
            latest_departure=400,
            shift_start=410,
            max_route_minutes=60,
            arrival_buffer=10,
            bus_capacity=32,
            time_limit_seconds=1.0,
        )

        self.assertEqual(res["status"], "OPTIMAL_OR_FEASIBLE")
        self.assertEqual(len(res["routes"]), 1)
        route = res["routes"][0]
        self.assertEqual(route["passengers_assigned"], 17)
        self.assertLessEqual(route["route_duration_minutes"], 60)

    def test_capacity_exceeded_rejection(self):
        time_matrix = [[0, 0], [10, 0]]
        demands = [0, 35]  # Exceeds 32 capacity
        res = optimize_routes(
            time_matrix=time_matrix,
            demands=demands,
            stop_names=["Depot", "Overcrowded Stop"],
            service_minutes=[0, 2],
            num_buses=1,
            earliest_departure=330,
            latest_departure=400,
            shift_start=410,
            bus_capacity=32,
        )
        self.assertEqual(res["status"], "DEMAND_EXCEEDS_CAPACITY")

if __name__ == "__main__":
    unittest.main()
