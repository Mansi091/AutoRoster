"""Generated roster sheet layout: Shift Time is filled, Bus No. is blank, Pickup Time is last."""

import sys
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import run_route_planner as rrp  # noqa: E402
from backend.app.core.config import config  # noqa: E402

SHIFT = next(iter(config.SHIFT_START_TIMES))
START_MIN = config.SHIFT_START_TIMES[SHIFT]


def _shift_result():
    def stop(name, minute, people):
        return {
            "stop_name": name, "scheduled_time": rrp._display_ampm(minute), "pickup_minute_of_day": minute,
            "employees": [{"emp_name": p, "emp_type": "DET", "shift": SHIFT, "mobile": "1"} for p in people],
        }
    return {
        "shift": SHIFT,
        "shift_start_minute_of_day": START_MIN,
        "outliers": [],
        "routes": [
            {"route_no": 1, "corridor_name": "R1", "stops": [stop("S1", START_MIN - 50, ["a", "b"])]},
            {"route_no": 2, "corridor_name": "R2", "stops": [stop("S2", START_MIN - 40, ["c"])]},
        ],
    }


class RosterWorkbookColumns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "roster.xlsx"
        rrp.generate_transport_roster_workbook([_shift_result()], path, facility={"name": "F", "lat": 0, "lon": 0})
        self.ws = load_workbook(path)[f"Shift {SHIFT}"]
        rows = list(self.ws.iter_rows(min_row=4, values_only=True))
        self.headers = list(rows[0])
        self.data = [dict(zip(self.headers, r)) for r in rows if r[0] != "Sr. No."]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_columns_present_and_pickup_time_last(self):
        self.assertIn("Shift Time", self.headers)
        self.assertIn("Bus No.", self.headers)
        self.assertEqual(self.headers[-1], "Pickup Time")

    def test_shift_time_is_filled_bus_number_is_blank_and_pickup_time_is_filled(self):
        self.assertEqual(len(self.data), 3)
        for row in self.data:
            self.assertEqual(row["Shift Time"], rrp._display_ampm(START_MIN))
            self.assertIsNone(row["Bus No."])
            self.assertTrue(row["Pickup Time"])

    def test_title_rows_span_all_columns(self):
        last = self.ws.cell(row=4, column=len(self.headers)).column_letter
        merged = {str(m) for m in self.ws.merged_cells.ranges}
        self.assertTrue({f"A1:{last}1", f"A2:{last}2", f"A3:{last}3"} <= merged)


if __name__ == "__main__":
    unittest.main()
