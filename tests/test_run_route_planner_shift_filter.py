import unittest

import pandas as pd

from run_route_planner import _filter_roster_to_shift


class TestRosterShiftFilter(unittest.TestCase):
    def test_all_shifts_keeps_the_complete_roster(self):
        roster = pd.DataFrame({"Name": ["Asha", "Bimal"], "Shift": ["A", "B"]})

        result = _filter_roster_to_shift(roster, "ALL")

        self.assertEqual(result["Name"].tolist(), ["Asha", "Bimal"])

    def test_single_shift_keeps_only_employees_in_that_shift(self):
        roster = pd.DataFrame({
            "Name": ["Asha", "Bimal", "Charu", "Dev"],
            "Shift": ["A", "Shift B", "A+B", "C"],
        })

        result = _filter_roster_to_shift(roster, "A")

        self.assertEqual(result["Name"].tolist(), ["Asha", "Charu"])


from run_route_planner import (  # noqa: E402
    attach_unrecognised_shift_employees,
    configured_shifts,
    parse_shift,
)

FIRST, SECOND, THIRD = configured_shifts()[:3]


class TestShiftValues(unittest.TestCase):
    def test_single_shift_values(self):
        for value, expected in [(FIRST, FIRST), (f"Shift {SECOND}", SECOND), (f" {THIRD.lower()} ", THIRD), (f"{SECOND} (Day)", SECOND)]:
            self.assertEqual(parse_shift(value), expected, value)

    def test_combined_shift_goes_to_earliest_listed_shift(self):
        for value in [f"{FIRST} + {SECOND}", f"{FIRST}+{SECOND}+{THIRD}", f"{SECOND}/{FIRST}"]:
            self.assertEqual(parse_shift(value), FIRST, value)
        self.assertEqual(parse_shift(f"{SECOND} + {THIRD}"), SECOND)

    def test_blank_and_unknown_shifts_are_not_guessed(self):
        for value in [None, float("nan"), "", "nan", "General", "Day", "X"]:
            self.assertIsNone(parse_shift(value), value)

    def test_filter_keeps_unrecognised_rows_for_reporting(self):
        roster = pd.DataFrame({"Name": ["Asha", "Bimal", "Charu"], "Shift": [FIRST, SECOND, "General"]})
        self.assertEqual(_filter_roster_to_shift(roster, SECOND)["Name"].tolist(), ["Bimal", "Charu"])

    def test_unrecognised_employees_reported_in_needs_attention(self):
        results = [{"shift": FIRST, "outliers": [], "total_employees_in_roster": 2}, {"shift": SECOND, "outliers": []}]
        out = attach_unrecognised_shift_employees(results, [{"emp_name": "Dev", "shift": ""}, {"emp_name": "Esha", "shift": "General"}])
        self.assertEqual([o["emp_name"] for o in out[0]["outliers"]], ["Dev", "Esha"])
        self.assertIn("No shift given", out[0]["outliers"][0]["reason"])
        self.assertIn("'General' is not recognised", out[0]["outliers"][1]["reason"])
        self.assertEqual(out[0]["total_employees_in_roster"], 4)
        self.assertEqual(results[0]["outliers"], [], "input is not mutated")
