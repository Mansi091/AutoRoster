"""Unit tests for the pipeline rules. Run:  python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
import step1_clean as s1  # noqa: E402
import step4_master as s4  # noqa: E402
import step6_grouping as s6  # noqa: E402
from utils import haversine_km, standardize_pickup, normalize_key  # noqa: E402


class TextAndCleaning(unittest.TestCase):
    def test_pickup_standardisation_fixes_spacing_and_typos(self):
        self.assertEqual(standardize_pickup("  CHOURAI,, parandwadi Road"), "CHOURAI, parandwadi Road")
        self.assertEqual(standardize_pickup("Shastri showk").lower(), "shastri chowk")
        self.assertEqual(standardize_pickup("Indrayani collage talegaon"), "Indrayani college talegaon")
        self.assertEqual(standardize_pickup("VADGAV, KHANDOBA MANDIR"), "Vadgaon, Khandoba Mandir")
        self.assertEqual(standardize_pickup(None), "")

    def test_alias_merges_same_stop(self):
        self.assertEqual(standardize_pickup("ecosity"), "Eco City")
        self.assertEqual(standardize_pickup("VARALE PHATA, TALEGAON"), "Varale Phata Talegaon")

    def test_normalize_key_ignores_case_and_punctuation(self):
        self.assertEqual(normalize_key("Ravet."), normalize_key("RAVET"))

    def test_shift_normalisation(self):
        self.assertEqual(s1.normalize_shift("a"), ("A", True))
        self.assertEqual(s1.normalize_shift("A + B"), ("A+B", True))
        self.assertEqual(s1.normalize_shift("A+B+C"), ("A+B+C", True))
        self.assertEqual(s1.normalize_shift(None), ("", False))
        self.assertEqual(s1.normalize_shift("D"), ("", False))

    def test_mobile_validation(self):
        self.assertEqual(s1.normalize_mobile(9822758812), ("9822758812", "ok"))
        self.assertEqual(s1.normalize_mobile(739914530), ("739914530", "invalid"))   # 9 digits
        self.assertEqual(s1.normalize_mobile(9999999999), ("9999999999", "invalid"))  # placeholder
        self.assertEqual(s1.normalize_mobile(None), ("", "missing"))

    def test_grade_normalisation(self):
        self.assertEqual(s1.normalize_grade("TEAMLEASE"), "TeamLease")
        self.assertEqual(s1.normalize_grade("ASSOCIATE"), "Associate")
        self.assertEqual(s1.normalize_grade("staff"), "Staff")


class Distance(unittest.TestCase):
    def test_haversine_known_distance(self):
        # Pune city centre to Chakan is roughly 30 km straight line
        d = haversine_km(18.5204, 73.8567, 18.7601, 73.8615)
        self.assertTrue(25 < d < 32, d)

    def test_zero_distance(self):
        self.assertAlmostEqual(haversine_km(18.5, 73.8, 18.5, 73.8), 0.0)


class Grouping(unittest.TestCase):
    def _nodes(self, specs):
        # specs: list of (name, lat, lon, count)
        return [{"key": n.lower(), "name": n, "lat": la, "lon": lo, "count": c, "emp_idx": []}
                for n, la, lo, c in specs]

    def test_bus_never_exceeds_capacity(self):
        # 10 stops at the same spot with 9 people each -> at most 3 per bus (27 seats)
        nodes = self._nodes([(f"S{i}", 18.5, 73.8, 9) for i in range(10)])
        original = s6.config.BUS_CAPACITY
        try:
            buses = s6.pack_pool(nodes)
        finally:
            s6.config.BUS_CAPACITY = original
        for bus in buses:
            self.assertLessEqual(s6.load_total(bus, nodes), config.BUS_CAPACITY)
        self.assertEqual(sum(len(b) for b in buses), 10)

    def test_distant_stops_are_not_forced_together(self):
        # two clusters ~50 km apart
        nodes = self._nodes([("North", 18.80, 73.85, 20), ("South", 18.10, 73.85, 20)])
        buses = s6.pack_pool(nodes)
        self.assertEqual(len(buses), 2)

    def test_bus_stops_are_compact(self):
        # A-B and B-C are ~4 km apart, A-C ~8 km: all three cannot share one 5 km-radius bus
        nodes = self._nodes([("A", 18.500, 73.850, 10), ("B", 18.536, 73.850, 10), ("C", 18.572, 73.850, 10)])
        buses = s6.pack_pool(nodes)
        for bus in buses:
            for a in bus:
                for b in bus:
                    if a < b:
                        d = s6.stop_distance(nodes[a], nodes[b])
                        self.assertLessEqual(d, config.MAX_CLUSTER_RADIUS_KM)

    def test_small_bus_merges_with_nearby_bus_when_seats_allow(self):
        nodes = self._nodes([("A1", 18.500, 73.850, 20), ("A2", 18.501, 73.851, 5)])
        buses = s6.pack_pool(nodes)
        # 20 + 5 = 25 fits in 32 and stops are very close -> one bus
        self.assertEqual(len(buses), 1)

    def test_small_bus_stays_separate_when_too_far(self):
        nodes = self._nodes([("A1", 18.50, 73.85, 20), ("B1", 18.90, 73.85, 5)])
        buses = s6.pack_pool(nodes)
        self.assertEqual(len(buses), 2)


class Approval(unittest.TestCase):
    def test_unapproved_stop_is_never_ready(self):
        row = {"pickup_key": "x", "pickup_point": "X", "routable_employees": 3, "status": "Needs review",
               "latitude": None, "longitude": None, "selected_candidate_no": 1, "search_query": "X"}
        out = s4.resolve_row(row, cache={"X": {"candidates": [{"lat": 18.5, "lon": 73.8, "formatted": "x"}]}})
        self.assertEqual(out["ready_for_routing"], "No")

    def test_approved_candidate_is_used(self):
        row = {"pickup_key": "x", "pickup_point": "X", "routable_employees": 3, "status": "Approved",
               "latitude": None, "longitude": None, "selected_candidate_no": 2, "search_query": "X"}
        cache = {"X": {"candidates": [{"lat": 18.1, "lon": 73.8, "formatted": "a"},
                                      {"lat": 18.5, "lon": 73.8, "formatted": "b"}]}}
        out = s4.resolve_row(row, cache)
        self.assertEqual(out["ready_for_routing"], "Yes")
        self.assertEqual(out["latitude"], 18.5)

    def test_coordinates_outside_pune_are_rejected(self):
        row = {"pickup_key": "x", "pickup_point": "X", "routable_employees": 3, "status": "Approved",
               "latitude": 28.6, "longitude": 77.2, "selected_candidate_no": None, "search_query": "X"}
        out = s4.resolve_row(row, cache={})
        self.assertEqual(out["ready_for_routing"], "No")
        self.assertIn("outside Pune", out["validation"])


if __name__ == "__main__":
    unittest.main()
