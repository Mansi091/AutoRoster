"""Unit tests for employee cleaning and standardization."""

import unittest
from backend.app.etl.transform.clean_employees import clean_employee_records
from backend.app.etl.transform.standardize_pickups import standardize_pickup_name
from backend.app.utils.text import format_mobile_number

class TestCleanEmployees(unittest.TestCase):
    def test_mobile_formatting(self):
        self.assertEqual(format_mobile_number("919876543210"), "9876543210")
        self.assertEqual(format_mobile_number("9876543210"), "9876543210")
        self.assertEqual(format_mobile_number("9876543210.0"), "9876543210")

    def test_standardize_pickup_name(self):
        self.assertEqual(standardize_pickup_name("bhakti shakti chowk"), "Bhakti Shakti Nigdi")
        self.assertEqual(standardize_pickup_name("talegaon stn"), "Talegaon Railway Station")

    def test_clean_employee_records(self):
        raw = [
            {"employee_id": "E001", "employee_name": "Test User", "shift": "Shift A", "pickup_point": "Nigdi Chowk", "mobile": "9876543210"}
        ]
        cleaned = clean_employee_records(raw)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0]["shift"], "A")
        self.assertEqual(cleaned[0]["mobile"], "9876543210")

if __name__ == "__main__":
    unittest.main()
