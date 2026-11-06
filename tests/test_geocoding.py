"""Tests for verified pickup geocoding and non-blocking roster routing."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import auto_resolve  # noqa: E402
import config  # noqa: E402
import geocoding  # noqa: E402
import run_route_planner as rrp  # noqa: E402
from backend.app.api import routes_dropoff, routes_plan  # noqa: E402
from backend.app.core.auth import require_authenticated_user  # noqa: E402
from backend.app.main import create_app  # noqa: E402


class PickupGeocoding(unittest.TestCase):
    def test_coordinate_master_uses_corrections_and_same_coordinate_aliases(self):
        results = geocoding.resolve_pickup_points(
            ["Xarbia,ambi", "Indrayani collage talegaon", "KATVI ROAD TALEGAON"]
        )
        for result in results.values():
            self.assertEqual(result["location_status"], "VERIFIED")
            self.assertIsNotNone(result["latitude"])
            self.assertIsNotNone(result["longitude"])

    def test_talegaon_low_confidence_candidate_stays_unverified(self):
        query = "Talegaon, Pune district, Maharashtra, India"
        cache = {
            query: {
                "candidates": [
                    {
                        "formatted": "Talegaon Dabhade, MH, India",
                        "lat": 18.7337107,
                        "lon": 73.6728593,
                        "confidence": 0.25,
                        "result_type": "city",
                    }
                ]
            }
        }
        with patch.object(geocoding, "load_cache", return_value=cache), \
             patch.object(geocoding, "_load_coordinate_index", return_value={}):
            result = geocoding.resolve_pickup_points(["Talegaon"])["Talegaon"]
        self.assertEqual(result["location_status"], "LOCATION_UNVERIFIED")
        self.assertIsNone(result["latitude"])

    def test_city_centroid_is_not_used_as_a_pickup_stop(self):
        query = "Talegaon, Pune district, Maharashtra, India"
        cache = {
            query: {
                "candidates": [
                    {
                        "formatted": "Talegaon Dabhade, MH, India",
                        "lat": 18.7337107,
                        "lon": 73.6728593,
                        "confidence": 0.99,
                        "result_type": "city",
                    }
                ]
            }
        }
        with patch.object(geocoding, "load_cache", return_value=cache), \
             patch.object(geocoding, "_load_coordinate_index", return_value={}):
            result = geocoding.resolve_pickup_points(["Talegaon"])["Talegaon"]
        self.assertEqual(result["location_status"], "LOCATION_UNVERIFIED")

    def test_wrong_locality_candidate_is_rejected_even_inside_pune_bounds(self):
        canonical = "Vadgaon, Khandoba Mandir"
        query = canonical + config.GEOAPIFY_QUERY_SUFFIX
        cache = {
            query: {
                "candidates": [
                    {
                        "formatted": "Khandoba Mandir, Mhalunge-Baner Road, Balewadi, Pune",
                        "lat": 18.5692765,
                        "lon": 73.7650538,
                        "confidence": 1,
                    }
                ]
            }
        }
        with patch.object(geocoding, "load_cache", return_value=cache), \
             patch.object(geocoding, "_load_coordinate_index", return_value={}):
            result = geocoding.resolve_pickup_points(
                ["VADGAV, KHANDOBA MANDIR"]
            )["VADGAV, KHANDOBA MANDIR"]
        self.assertEqual(result["location_status"], "LOCATION_UNVERIFIED")
        self.assertIsNone(result["longitude"])

    def test_high_confidence_relevant_search_result_is_cached_and_accepted(self):
        candidate = {
            "formatted": "Talegaon Dabhade, Maharashtra, India",
            "lat": 18.7337107,
            "lon": 73.6728593,
            "confidence": 0.9,
            "result_type": "amenity",
        }
        with (
            patch.object(geocoding, "load_cache", return_value={}),
            patch.object(geocoding, "_load_coordinate_index", return_value={}),
            patch.object(geocoding.config, "GEOAPIFY_API_KEY", "test-key"),
            patch.object(geocoding, "geoapify_lookup", return_value=[candidate]),
            patch.object(geocoding, "save_cache") as save_cache,
        ):
            result = geocoding.resolve_pickup_points(["Talegaon"])["Talegaon"]
        self.assertEqual(result["location_status"], "VERIFIED")
        self.assertEqual(result["coordinate_source"], "Geoapify")
        save_cache.assert_called_once()

    def test_auto_resolver_does_not_approve_unverified_or_fallback_coordinates(self):
        master = pd.DataFrame(
            [
                {
                    "pickup_point": "Old Fallback",
                    "status": "Approved",
                    "latitude": 18.7,
                    "longitude": 73.7,
                    "reviewed_by": "auto_local_fallback",
                },
                {
                    "pickup_point": "Unmatched Stop",
                    "status": "Needs review",
                    "latitude": None,
                    "longitude": None,
                    "reviewed_by": None,
                },
            ]
        )
        unresolved = {
            name: {
                "location_status": "LOCATION_UNVERIFIED",
                "latitude": None,
                "longitude": None,
                "resolution_reason": "No unique verified match.",
            }
            for name in ("Old Fallback", "Unmatched Stop")
        }
        with (
            patch.object(auto_resolve, "read_sheet", return_value=pd.DataFrame()),
            patch.object(auto_resolve, "load_master", return_value=master.copy()),
            patch.object(auto_resolve, "resolve_pickup_points", return_value=unresolved),
            patch.object(auto_resolve, "save_master") as save_master,
        ):
            auto_resolve.resolve_all()

        saved = save_master.call_args.args[0]
        self.assertEqual(saved["status"].tolist(), ["Needs review", "Needs review"])
        self.assertTrue(saved["latitude"].isna().all())
        self.assertTrue(saved["longitude"].isna().all())


class UnverifiedRosterFlow(unittest.TestCase):
    def setUp(self):
        self.api = create_app()
        self.api.dependency_overrides[require_authenticated_user] = lambda: {"id": "test-supervisor"}
        self.client = TestClient(self.api)

    def tearDown(self):
        self.client.close()
        self.api.dependency_overrides.clear()

    def test_planning_input_retains_unverified_employee_for_review(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "roster.xlsx"
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.append(["Employee transport roster"])
            worksheet.append(["Name", "Shift", "Bus Stop", "Mob. No.", "GRADE"])
            worksheet.append(["Routed Employee", "A", "Known Stop", "111", "G1"])
            worksheet.append(["Review Employee", "A", "Unknown Stop", "222", "G2"])
            workbook.save(path)

            locations = {
                "Known Stop": {
                    "location_status": "VERIFIED",
                    "latitude": 18.735,
                    "longitude": 73.675,
                },
                "Unknown Stop": {
                    "location_status": "LOCATION_UNVERIFIED",
                    "latitude": None,
                    "longitude": None,
                    "resolution_reason": "No matching verified location.",
                },
            }
            with patch.object(rrp, "load_all_verified_stops", return_value={}), \
                    patch.object(rrp, "_master_match", return_value=None):
                result = rrp.prepare_planning_input(
                    path,
                    {"name": "Test Facility", "lat": 18.7, "lon": 73.7},
                    pickup_resolver=lambda names, facility: locations,
                )

        self.assertEqual([employee["emp_name"] for employee in result["all_employees"]], [
            "Routed Employee", "Review Employee",
        ])
        self.assertEqual(
            result["resolution_details"]["Unknown Stop"]["location_status"],
            "LOCATION_UNVERIFIED",
        )

    def test_resolve_pickups_api_validates_payload(self):
        resolved = {
            "Test Stop": {
                "location_status": "VERIFIED",
                "latitude": 18.735,
                "longitude": 73.675,
            }
        }
        with patch.object(routes_dropoff, "get_current_dropoff_data", return_value={"confirmed": True}), \
                patch.object(routes_plan, "resolve_pickup_coordinates", return_value=resolved):
            response = self.client.post("/api/resolve_pickups", json={"pickup_points": ["Test Stop"]})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json()["locations"]["Test Stop"]["location_status"],
                "VERIFIED",
            )

            invalid = self.client.post("/api/resolve_pickups", json={"pickup_points": "Test Stop"})
            self.assertEqual(invalid.status_code, 400)

    def test_excel_export_returns_current_manifest_workbook(self):
        response = self.client.post(
            "/api/export_excel",
            json={
                "routes": [],
                "employees": [
                    {
                        "sr_no": 1,
                        "employee_id": "2",
                        "employee_name": "Review Employee",
                        "shift": "A",
                        "assigned_bus": "Unassigned",
                        "pickup_point": "Talegaon",
                        "mobile": "222",
                        "location_status": "LOCATION_UNVERIFIED",
                        "resolution_reason": "Ambiguous locality.",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 200)
        workbook = load_workbook(io.BytesIO(response.content), read_only=True)
        self.assertIn("Fleet Summary", workbook.sheetnames)
        self.assertIn("Passenger Manifest", workbook.sheetnames)


if __name__ == "__main__":
    unittest.main()
