"""Unit tests for spatial geofencing and coordinate resolution."""

import unittest
from backend.app.utils.geo import is_within_pune_geofence, haversine_km, get_radial_corridor
from backend.app.etl.transform.candidate_scoring import evaluate_coordinate_candidate
from backend.app.db.repositories.stops_repo import stops_repo

class TestGeocoding(unittest.TestCase):
    def test_pune_geofence(self):
        # Nigdi (within boundary)
        self.assertTrue(is_within_pune_geofence(18.65, 73.76))
        # Delhi (outside boundary)
        self.assertFalse(is_within_pune_geofence(28.61, 77.20))
        # Null
        self.assertFalse(is_within_pune_geofence(None, None))

    def test_haversine(self):
        # Nigdi to Talegaon ~ 15 km
        d = haversine_km(18.65, 73.76, 18.776, 73.694)
        self.assertGreater(d, 10.0)
        self.assertLess(d, 25.0)

    def test_radial_corridor(self):
        # Katraj/Sangvi is South
        self.assertEqual(get_radial_corridor(18.57, 73.80), "South Corridor")
        # Chakan is East
        self.assertEqual(get_radial_corridor(18.75, 73.85), "East Corridor")

    def test_candidate_scoring_verified(self):
        status, reason = evaluate_coordinate_candidate(18.65, 73.76)
        self.assertEqual(status, "VERIFIED")

    def test_candidate_scoring_outside_radius(self):
        # Beyond 50 km from plant
        status, reason = evaluate_coordinate_candidate(18.20, 73.80)
        self.assertEqual(status, "OUTSIDE_SERVICE_RADIUS")

if __name__ == "__main__":
    unittest.main()
