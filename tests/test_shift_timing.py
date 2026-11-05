"""Shift start vs gate deadline (handoff item 4a).

Shift start time is the source of truth. The gate deadline is
shift_start - arrival buffer, where the buffer may come from a scenario override.
The client rule (first pickup no earlier than PICKUP_WINDOW_HARD before shift
start) must hold for every bus.

Runs offline: OSRM is replaced by a synthetic road model built from the test's
own coordinates. Every limit is read from config; nothing is hardcoded.
"""

import math
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import run_route_planner as rrp  # noqa: E402
from backend.app.core.config import config  # noqa: E402

# Synthetic geography: a facility and stops on a ring, spaced well beyond the merge radius.
FACILITY = {"name": "Test Facility", "lat": 18.5, "lon": 73.8}
STOP_COUNT = 8
EMPLOYEES_PER_STOP = 3
EARTH_M_PER_DEG = 111_320.0


def _stop_coordinates():
    spacing_m = rrp.MERGE_RADIUS_METERS * 4
    coords = []
    for i in range(STOP_COUNT):
        radius_m = spacing_m * (2 + i)
        angle = 2 * math.pi * i / STOP_COUNT
        coords.append((
            FACILITY["lat"] + radius_m * math.cos(angle) / EARTH_M_PER_DEG,
            FACILITY["lon"] + radius_m * math.sin(angle) / EARTH_M_PER_DEG,
        ))
    return coords


STOPS = _stop_coordinates()


def _speed_mps(max_ride_sec: float) -> float:
    """Pick a speed so the farthest stop's solo drive takes most of the ride limit."""
    farthest_m = max(rrp.haversine_distance_m(s, (FACILITY["lat"], FACILITY["lon"])) for s in STOPS)
    return farthest_m / (0.8 * max_ride_sec)


def _road_model(speed_mps: float):
    def travel_sec(a, b) -> int:
        return int(round(rrp.haversine_distance_m(a, b) / speed_mps))

    def fake_matrix(nodes):
        pts = [(n["lat"], n["lon"]) for n in nodes]
        times = [[travel_sec(a, b) for b in pts] for a in pts]
        dists = [[rrp.haversine_distance_m(a, b) for b in pts] for a in pts]
        return times, dists

    def fake_geometry(waypoints, continue_straight=None):
        legs = list(zip(waypoints, waypoints[1:]))
        leg_sec = [float(travel_sec(a, b)) for a, b in legs]
        leg_m = [rrp.haversine_distance_m(a, b) for a, b in legs]
        return {
            "coordinates": list(waypoints),
            "geometry": list(waypoints),
            "distance_km": sum(leg_m) / 1000.0,
            "distance_m": sum(leg_m),
            "duration_min": sum(leg_sec) / 60.0,
            "duration_sec": sum(leg_sec),
            "leg_durations_min": [s / 60.0 for s in leg_sec],
            "leg_durations_sec": leg_sec,
            "leg_distances_km": [m / 1000.0 for m in leg_m],
            "leg_distances_m": leg_m,
        }

    return fake_matrix, fake_geometry


def _roster(shift: str):
    lookup, employees = {}, []
    for i, (lat, lon) in enumerate(STOPS):
        name = f"Test Stop {i}"
        lookup[rrp._stop_key(name)] = {"name": name, "lat": lat, "lon": lon}
        for j in range(EMPLOYEES_PER_STOP):
            employees.append({
                "emp_name": f"Emp {i}-{j}", "shift": shift, "raw_stop": name, "mobile": f"{i}{j}",
            })
    return employees, lookup


def _solve(shift, overrides=None, shift_start_times=None):
    buffer_min = float((overrides or {}).get("ARRIVAL_BUFFER_MINUTES", rrp.ARRIVAL_BUFFER_MIN))
    window_hard = float((overrides or {}).get("PICKUP_WINDOW_HARD", rrp.PICKUP_WINDOW_HARD))
    fake_matrix, fake_geometry = _road_model(_speed_mps((window_hard - buffer_min) * 60.0))
    employees, lookup = _roster(shift)
    with mock.patch.object(rrp, "get_road_matrix", side_effect=fake_matrix), \
            mock.patch.object(rrp, "fetch_osrm_route_geometry", side_effect=fake_geometry), \
            mock.patch.object(rrp, "evaluate_coordinate_candidate", return_value=("VERIFIED", "")):
        return rrp.solve_shift_routes(
            shift, employees, lookup, FACILITY,
            shift_start_times=shift_start_times,
            plan_history_mode="ignore",
            scenario_overrides=overrides or {},
        )


# Route durations are reported rounded to 0.1 s; allow exactly that rounding, in minutes.
DURATION_ROUNDING_MIN = 0.05 / 60.0


class ShiftTimingHelpers(unittest.TestCase):
    def test_deadline_is_start_minus_buffer(self):
        start = config.SHIFT_START_TIMES["A"]
        self.assertEqual(rrp.arrival_deadline_min(start, rrp.ARRIVAL_BUFFER_MIN), start - rrp.ARRIVAL_BUFFER_MIN)

    def test_config_start_times_match_configured_deadlines(self):
        for shift, deadline in config.SHIFT_ARRIVAL_DEADLINES.items():
            self.assertEqual(config.SHIFT_START_TIMES[shift], (deadline + rrp.ARRIVAL_BUFFER_MIN) % 1440)

    def test_submitted_start_time_wins_over_config(self):
        shift = next(iter(config.SHIFT_START_TIMES))
        submitted = (config.SHIFT_START_TIMES[shift] + 37) % 1440
        self.assertEqual(rrp.resolve_shift_start_min(shift, {shift: submitted}), submitted)
        self.assertEqual(rrp.resolve_shift_start_min(shift, None), config.SHIFT_START_TIMES[shift])


class ShiftTimingSolver(unittest.TestCase):
    shift = next(iter(config.SHIFT_START_TIMES))

    def _assert_timing(self, result, shift_start, buffer_min, client_window_hard):
        self.assertGreater(result["buses_used"], 0)
        self.assertEqual(result["shift_start_minute_of_day"], shift_start)
        self.assertAlmostEqual(result["plant_deadline_minute_of_day"], shift_start - buffer_min, places=6)
        for route in result["routes"]:
            first_pickup = route["stops"][0]["pickup_minute_exact"]
            gate_arrival = first_pickup + route["route_duration_sec"] / 60.0
            self.assertAlmostEqual(gate_arrival, shift_start - buffer_min, delta=DURATION_ROUNDING_MIN + 1e-3)
            self.assertGreaterEqual(first_pickup, shift_start - client_window_hard - 1e-9)
            self.assertAlmostEqual(route["first_pickup_before_shift_min"], shift_start - first_pickup, places=2)

    def test_config_buffer(self):
        result = _solve(self.shift)
        self._assert_timing(result, config.SHIFT_START_TIMES[self.shift], rrp.ARRIVAL_BUFFER_MIN, rrp.PICKUP_WINDOW_HARD)

    def test_buffer_override_moves_gate_arrival_not_shift_start(self):
        override_buffer = rrp.ARRIVAL_BUFFER_MIN / 2.0
        result = _solve(self.shift, {"ARRIVAL_BUFFER_MINUTES": override_buffer})
        self.assertEqual(result["arrival_buffer_minutes"], override_buffer)
        self._assert_timing(result, config.SHIFT_START_TIMES[self.shift], override_buffer, rrp.PICKUP_WINDOW_HARD)

    def test_submitted_start_time_is_used(self):
        submitted = (config.SHIFT_START_TIMES[self.shift] + 37) % 1440
        result = _solve(self.shift, shift_start_times={self.shift: submitted})
        self._assert_timing(result, submitted, rrp.ARRIVAL_BUFFER_MIN, rrp.PICKUP_WINDOW_HARD)


class ScenarioCompliance(unittest.TestCase):
    def _row(self, windows):
        shift_results = [{
            "shift": "X", "buses_used": len(windows), "assigned_passengers": len(windows),
            "routes": [{"first_pickup_before_shift_min": w, "route_duration_min": 0.0} for w in windows],
        }]
        return rrp.summarize_scenario("s", "", {}, shift_results, len(windows), rrp.PICKUP_WINDOW_HARD)

    def test_within_client_rule_is_compliant(self):
        row = self._row([rrp.PICKUP_WINDOW_HARD - 1, rrp.PICKUP_WINDOW_HARD])
        self.assertEqual(row["client_rule_status"], "COMPLIANT")
        self.assertEqual(row["buses_by_shift"], {"X": 2})

    def test_beyond_client_rule_breaks_it(self):
        row = self._row([rrp.PICKUP_WINDOW_HARD + 0.1])
        self.assertEqual(row["client_rule_status"], "BREAKS CLIENT RULE")


if __name__ == "__main__":
    unittest.main()
