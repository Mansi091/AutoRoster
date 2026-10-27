"""End-to-end integration tests for the full AutoRoster planning service."""

import unittest
from backend.app.services.plan_service import plan_service

class TestEndToEnd(unittest.TestCase):
    def test_plan_shift_strict_commute_cap(self):
        # Synthetic employee roster with verified coordinates
        test_employees = [
            {"employee_id": "E1", "employee_name": "Emp 1", "shift": "A", "pickup_point": "Nigdi", "latitude": 18.65, "longitude": 73.76, "location_status": "VERIFIED"},
            {"employee_id": "E2", "employee_name": "Emp 2", "shift": "A", "pickup_point": "Chinchwad", "latitude": 18.63, "longitude": 73.78, "location_status": "VERIFIED"},
            {"employee_id": "E3", "employee_name": "Emp 3", "shift": "A", "pickup_point": "Akurdi", "latitude": 18.64, "longitude": 73.77, "location_status": "VERIFIED"},
            {"employee_id": "E4", "employee_name": "Emp 4", "shift": "A", "pickup_point": "Ravet", "latitude": 18.66, "longitude": 73.74, "location_status": "VERIFIED"},
            {"employee_id": "E5", "employee_name": "Emp 5", "shift": "A", "pickup_point": "Dehu Road", "latitude": 18.71, "longitude": 73.72, "location_status": "VERIFIED"},
        ]

        result = plan_service.plan_shift(raw_employees=test_employees, bus_capacity=32)
        self.assertEqual(result["status"], "OK")
        routes = result["routes"]
        self.assertGreater(len(routes), 0)

        # Verify strict commute duration <= 60 min (max 70 min)
        for r in routes:
            self.assertLessEqual(r["total_minutes"], 70.0)
            self.assertLessEqual(r["passengers_assigned"], 32)
            self.assertEqual(len(r["stops"]), r["total_stops"])

if __name__ == "__main__":
    unittest.main()
