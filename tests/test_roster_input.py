import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import run_route_planner as rrp  # noqa: E402


class PlanningRosterInputTests(unittest.TestCase):
    def _read_workbook(self, rows):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "input_roster.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        for row in rows:
            worksheet.append(row)
        workbook.save(path)
        return rrp._read_planning_roster(path)

    def test_reads_standard_input_headers_after_title_row(self):
        roster = self._read_workbook([
            ["Employee Transport Roster"],
            ["Name", "Shift", "Bus Stop", "GRADE", "Mob. No."],
            ["Asha", "A", "Central Stop", "Staff", "1234567890"],
        ])

        self.assertEqual(roster.loc[0, "Name"], "Asha")
        self.assertEqual(roster.loc[0, "Shift"], "A")
        self.assertEqual(roster.loc[0, "Bus Stop"], "Central Stop")
        self.assertEqual(roster.loc[0, "GRADE"], "Staff")
        self.assertEqual(roster.loc[0, "Mob. No."], 1234567890)

    def test_reads_generated_roster_headers_below_multiple_title_rows(self):
        roster = self._read_workbook([
            ["Roster Sheet — Shift A"],
            ["Drop-off Location: Demo Facility"],
            ["Supervisor Details: __________________"],
            [
                "Sr. No.", "Employee Type", "Employee Name", "Shift", "Route Name",
                "Pickup point", "Mobile No.", "Route No.", "Vehicle Required",
                "Driver Name", "Driver Contact", "Pickup Time",
            ],
            [
                1, "Staff", "Tejas", "A", "Demo route", "Central Stop",
                "1234567890", 1, "32-Seater Bus", "", "", "05:42 AM",
            ],
        ])

        self.assertEqual(roster.loc[0, "Name"], "Tejas")
        self.assertEqual(roster.loc[0, "Shift"], "A")
        self.assertEqual(roster.loc[0, "Bus Stop"], "Central Stop")
        self.assertEqual(roster.loc[0, "GRADE"], "Staff")
        self.assertEqual(roster.loc[0, "Mob. No."], 1234567890)

    def test_reports_missing_name_or_pickup_headers(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "invalid_roster.xlsx"
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(["Employee Name", "Shift"])
        worksheet.append(["Asha", "A"])
        workbook.save(path)

        with self.assertRaisesRegex(ValueError, "Could not find roster headers"):
            rrp._read_planning_roster(path)


if __name__ == "__main__":
    unittest.main()
