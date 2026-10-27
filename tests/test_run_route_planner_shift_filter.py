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
