"""Multi-shift bus route planner using OR-Tools and OSRM.

Operating limits, shift deadlines, pickup merge radius, dwell assumptions, and
solver budget are read from the project configuration. The planner returns the
best feasible heuristic plan among the candidates it validates; it does not
claim a mathematical proof of global optimality.
"""

try:
    import google.protobuf.runtime_version
    google.protobuf.runtime_version.ValidateProtobufRuntimeVersion = lambda *args, **kwargs: None
except (ImportError, AttributeError):
    pass

import os
import re
import math
import datetime
import json
import difflib
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

import pandas as pd
import requests
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from backend.app.core.config import config
from backend.app.etl.transform.candidate_scoring import evaluate_coordinate_candidate

BUS_CAPACITY = config.FLEET.standard_bus_capacity
MAX_RIDE_TIME_MIN = config.COMMUTE.hard_max_commute_minutes
TARGET_RIDE_TIME_MIN = config.COMMUTE.target_commute_minutes
DWELL_TIME_MIN = config.COMMUTE.dwell_minutes_per_stop
BOARDING_SECONDS_PER_PASSENGER = config.COMMUTE.dwell_seconds_per_passenger
MERGE_RADIUS_METERS = config.PICKUP_MERGE_RADIUS_METERS
SOLVER_TIME_LIMIT_SECONDS = config.ROUTE_SOLVER_TIME_LIMIT_SECONDS


def _display_clock(minutes: int) -> str:
    hour, minute = (minutes // 60) % 24, minutes % 60
    return f"{hour:02d}:{minute:02d}"


def _display_ampm(minutes: int) -> str:
    hour, minute = (minutes // 60) % 24, minutes % 60
    return f"{hour % 12 or 12:02d}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


SHIFT_CONFIG = {
    shift: {
        "deadline_min": deadline,
        "deadline": _display_clock(deadline),
        "in_time": _display_clock(deadline + config.COMMUTE.arrival_buffer_minutes),
        "display_deadline": _display_ampm(deadline),
    }
    for shift, deadline in config.SHIFT_ARRIVAL_DEADLINES.items()
}


def _stop_dwell_minutes(node_idx: int, nodes: List[Dict[str, Any]]) -> float:
    demand = int(nodes[node_idx].get("demand", 0))
    return DWELL_TIME_MIN + (BOARDING_SECONDS_PER_PASSENGER * demand / 60.0)


def _stop_key(name: str) -> str:
    text = str(name or "").lower()
    text = re.sub(r"\b(chawk|chouk|chowck)\b", "chowk", text)
    text = re.sub(r"\b(phatta|fata)\b", "phata", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _master_match(name: str, lookup: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Match an exact normalized master name, then a high-confidence typo only."""
    key = _stop_key(name)
    if key in lookup:
        return lookup[key]
    matches = sorted(
        ((difflib.SequenceMatcher(None, key, known).ratio(), item) for known, item in lookup.items()),
        key=lambda match: match[0],
        reverse=True,
    )
    if not matches or matches[0][0] < 0.94:
        return None
    best_score, best = matches[0]
    tied_locations = {
        (round(item["lat"], 6), round(item["lon"], 6))
        for score, item in matches
        if best_score - score < 0.04
    }
    return best if len(tied_locations) == 1 else None


def haversine_distance_m(c1: Tuple[float, float], c2: Tuple[float, float]) -> float:
    lat1, lon1 = c1
    lat2, lon2 = c2
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def merge_nearby_pickups(stop_groups: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministically merge only compact pickup groups that fit one bus."""
    clusters = [
        {"members": [stop], "demand": len(stop["employees"]), "employees": list(stop["employees"])}
        for stop in sorted(stop_groups.values(), key=lambda item: (item["lat"], item["lon"], item["name"]))
    ]

    while True:
        best_pair = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                left, right = clusters[i], clusters[j]
                if left["demand"] + right["demand"] > BUS_CAPACITY:
                    continue
                cross_distances = [
                    haversine_distance_m((a["lat"], a["lon"]), (b["lat"], b["lon"]))
                    for a in left["members"] for b in right["members"]
                ]
                if not cross_distances or max(cross_distances) > MERGE_RADIUS_METERS:
                    continue
                score = (max(cross_distances), sum(cross_distances), i, j)
                if best_pair is None or score < best_pair[0]:
                    best_pair = (score, i, j)

        if best_pair is None:
            break

        _, i, j = best_pair
        left, right = clusters[i], clusters[j]
        merged = {
            "members": left["members"] + right["members"],
            "demand": left["demand"] + right["demand"],
            "employees": left["employees"] + right["employees"],
        }
        clusters = [cluster for idx, cluster in enumerate(clusters) if idx not in (i, j)]
        clusters.append(merged)

    result = []
    for cluster in clusters:
        members = cluster["members"]
        anchor = min(
            members,
            key=lambda item: (
                sum(haversine_distance_m((item["lat"], item["lon"]), (other["lat"], other["lon"])) for other in members),
                item["name"],
            ),
        )
        employees = sorted(cluster["employees"], key=lambda employee: employee.get("sr_no", 0))
        for part_idx, start in enumerate(range(0, len(employees), BUS_CAPACITY), 1):
            chunk = employees[start:start + BUS_CAPACITY]
            result.append({
                "name": anchor["name"] if len(employees) <= BUS_CAPACITY else f"{anchor['name']} (Part {part_idx})",
                "lat": anchor["lat"],
                "lon": anchor["lon"],
                "demand": len(chunk),
                "employees": chunk,
            })
    return result


def load_all_verified_stops() -> Dict[str, Dict[str, Any]]:
    """Load cached verified stops and the reviewed pickup master into one lookup."""
    lookup = {}

    # Geocoder-confirmed roster aliases are persisted in the shared physical
    # stop cache, then reused on later uploads without another API lookup.
    seed_path = config.MASTER_STOPS_FILE
    if seed_path.exists():
        try:
            with seed_path.open("r", encoding="utf-8") as seed_file:
                seeded_stops = json.load(seed_file)
            if isinstance(seeded_stops, dict):
                for name, coords in seeded_stops.items():
                    try:
                        lat, lon = float(coords[0]), float(coords[1])
                    except (TypeError, ValueError, IndexError):
                        continue
                    key = _stop_key(name)
                    if key:
                        lookup[key] = {"name": str(name).strip(), "lat": lat, "lon": lon}
        except Exception as exc:
            print(f"[Stop cache] Could not load cached verified stops: {exc}")

    # Pickup point master (authoritative Excel repository of verified coordinates & proper spaced names)
    master_path = config.DATA_DIR / "manual" / "pickup_point_master.xlsx"
    if master_path.exists():
        df_master = pd.read_excel(master_path)
        for _, r in df_master.iterrows():
            name = str(r.get("pickup_point", "")).strip()
            raw_key = str(r.get("pickup_key", "")).strip()
            if not name or name.lower() == "nan":
                continue
            try:
                lat = float(r["latitude"])
                lon = float(r["longitude"])
            except (ValueError, TypeError):
                continue

            item = {"name": name, "lat": lat, "lon": lon}

            # Index by normalized pickup_point (proper spaces retained in item["name"])
            k_name = _stop_key(name)
            if k_name:
                lookup[k_name] = item

            # Also index by pickup_key alias
            k_alias = _stop_key(raw_key)
            if k_alias:
                lookup[k_alias] = item

    return lookup


OSRM_CACHE_FILE = config.CACHE_DIR / "osrm_matrix_cache.json"
OSRM_GEOM_CACHE_FILE = config.CACHE_DIR / "osrm_geom_cache.json"


def load_json_cache(file_path: Path) -> dict:
    if file_path.exists():
        try:
            import json
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_json_cache(file_path: Path, data: dict):
    try:
        import json
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


class UnreachableRoadRouteError(RuntimeError):
    """OSRM confirms that a requested waypoint sequence has no road path."""


def get_road_matrix(nodes: List[Dict[str, Any]]) -> Tuple[List[List[int]], List[List[float]]]:
    """Generates true road travel time (minutes) and distance (km) matrix using OSRM Table API."""
    n = len(nodes)
    if n <= 1:
        return [[0]], [[0.0]]

    # Check disk cache
    cache = load_json_cache(OSRM_CACHE_FILE)
    # Include endpoint and full coordinate precision so a facility change or
    # a nearby-but-distinct pickup cannot reuse a stale road matrix.
    coords_key = config.OSRM_TABLE_URL + "|osrm-table-v4:" + ";".join(
        [f"{n_item['lat']:.6f},{n_item['lon']:.6f}" for n_item in nodes]
    )
    if coords_key in cache:
        cached = cache[coords_key]
        durations, distances = cached.get("durations"), cached.get("distances")
        if (
            isinstance(durations, list) and isinstance(distances, list)
            and len(durations) == n and len(distances) == n
            and all(isinstance(row, list) and len(row) == n for row in durations)
            and all(isinstance(row, list) and len(row) == n for row in distances)
        ):
            return durations, distances

    # Call OSRM Table API for real road travel times and road distances along mapped roads
    coords_str = ";".join([f"{n_item['lon']:.6f},{n_item['lat']:.6f}" for n_item in nodes])
    url = f"{config.OSRM_TABLE_URL.rstrip('/')}/{coords_str}?annotations=distance,duration"
    response_status = "unknown response"
    try:
        res = requests.get(url, timeout=12, headers={"User-Agent": "AutoRoster/1.0"})
        response_status = f"HTTP {res.status_code}"
        if res.status_code == 200:
            data = res.json()
            response_status = str(data.get("code", "unknown OSRM response"))
            if data.get("code") == "Ok" and "durations" in data and "distances" in data:
                raw_durations = data["durations"]
                raw_distances = data["distances"]
                if (
                    not isinstance(raw_durations, list) or not isinstance(raw_distances, list)
                    or len(raw_durations) != n or len(raw_distances) != n
                    or any(not isinstance(row, list) or len(row) != n for row in raw_durations)
                    or any(not isinstance(row, list) or len(row) != n for row in raw_distances)
                ):
                    raise RuntimeError("OSRM returned a malformed travel-time matrix")
                durations = []
                distances = []
                for i in range(n):
                    dur_row = []
                    dist_row = []
                    for j in range(n):
                        if i == j:
                            dur_row.append(0)
                            dist_row.append(0.0)
                        else:
                            sec = raw_durations[i][j]
                            meters = raw_distances[i][j]
                            if sec is not None and meters is not None:
                                time_min = max(2, int(math.ceil(sec / 60.0)))
                                dist_km = round(meters / 1000.0, 2)
                            else:
                                # Treat missing road connections as infeasible instead of
                                # silently substituting a straight-line estimate.
                                dist_km = 1_000_000.0
                                time_min = int(MAX_RIDE_TIME_MIN) + 1
                            dur_row.append(time_min)
                            dist_row.append(dist_km)
                    durations.append(dur_row)
                    distances.append(dist_row)

                cache[coords_key] = {"durations": durations, "distances": distances}
                save_json_cache(OSRM_CACHE_FILE, cache)
                print(f"[OSRM] Computed true road matrix for {n} nodes.")
                return durations, distances
    except Exception as e:
        raise RuntimeError(f"Road-time matrix unavailable from OSRM; refusing to estimate routes: {e}") from e

    raise RuntimeError(f"OSRM could not build a road-time matrix ({response_status}).")


def fetch_osrm_route_geometry(waypoints: List[Tuple[float, float]]) -> Dict[str, Any]:
    """Fetches real turn-by-turn road geometry coordinates along paved roads via OSRM."""
    if len(waypoints) < 2:
        return {"coordinates": waypoints, "distance_km": 0.0, "duration_min": 0.0}

    cache = load_json_cache(OSRM_GEOM_CACHE_FILE)
    w_key = config.OSRM_ROUTE_URL + "|osrm-route-v2:" + ";".join(
        [f"{lat:.6f},{lon:.6f}" for lat, lon in waypoints]
    )
    if w_key in cache and isinstance(cache[w_key], dict):
        cached = cache[w_key]
        expected_legs = len(waypoints) - 1
        if (
            len(cached.get("leg_durations_min", [])) == expected_legs
            and len(cached.get("leg_distances_km", [])) == expected_legs
            and len(cached.get("coordinates", [])) >= 2
        ):
            return cached

    coords_str = ";".join([f"{lon:.6f},{lat:.6f}" for lat, lon in waypoints])
    url = f"{config.OSRM_ROUTE_URL.rstrip('/')}/{coords_str}?overview=full&geometries=geojson"
    try:
        res = requests.get(url, timeout=10, headers={"User-Agent": "AutoRoster/1.0"})
        if res.status_code == 200:
            data = res.json()
            if data.get("code") == "Ok" and data.get("routes"):
                r = data["routes"][0]
                expected_legs = len(waypoints) - 1
                if len(r.get("legs", [])) != expected_legs:
                    raise RuntimeError("OSRM returned an unexpected number of route legs")
                geo_coords = [[round(pt[1], 6), round(pt[0], 6)] for pt in r["geometry"]["coordinates"]]
                if len(geo_coords) < 2:
                    raise RuntimeError("OSRM returned empty route geometry")
                result = {
                    "coordinates": geo_coords,
                    "distance_km": round(r["distance"] / 1000.0, 1),
                    "duration_min": round(r["duration"] / 60.0, 1),
                    "leg_durations_min": [round(leg["duration"] / 60.0, 1) for leg in r.get("legs", [])],
                    "leg_distances_km": [round(leg["distance"] / 1000.0, 2) for leg in r.get("legs", [])],
                }
                cache[w_key] = result
                save_json_cache(OSRM_GEOM_CACHE_FILE, cache)
                return result
            if data.get("code") in {"NoRoute", "NoSegment"}:
                raise UnreachableRoadRouteError(
                    f"OSRM found no connected road route for this waypoint sequence ({data.get('code')})."
                )
            raise RuntimeError(f"OSRM route request failed: {data.get('message') or data.get('code', 'unknown response')}")
        raise RuntimeError(f"OSRM route request returned HTTP {res.status_code}.")
    except UnreachableRoadRouteError:
        raise
    except Exception as e:
        raise RuntimeError(f"OSRM route service is unavailable: {e}") from e

    raise RuntimeError("OSRM could not return mapped road geometry; refusing to draw straight-line routes.")



def build_corridor_label(stops: List[str]) -> str:
    """Builds a human-readable corridor name from prominent landmarks along the route."""
    if not stops:
        return "Plant Direct"
    clean_names = []
    for s in stops:
        # Take the main locality or landmark name
        part = s.split(",")[0].strip()
        part = re.sub(r"\(.*?\)", "", part).strip()
        if part and part not in clean_names:
            clean_names.append(part)
    return " / ".join(clean_names[:4])


def repair_route_sequences(
    stop_indices: List[int],
    nodes: List[Dict[str, Any]],
    facility: Dict[str, Any],
) -> Tuple[List[Tuple[List[int], Dict[str, Any]]], List[int]]:
    """Validate routes on mapped OSRM legs and split any route over the ride cap."""
    route_cache: Dict[Tuple[int, ...], Tuple[Dict[str, Any], float]] = {}

    def evaluate(sequence: List[int]) -> Tuple[Dict[str, Any], float]:
        key = tuple(sequence)
        if key not in route_cache:
            waypoints = [(nodes[idx]["lat"], nodes[idx]["lon"]) for idx in sequence]
            waypoints.append((facility["lat"], facility["lon"]))
            info = fetch_osrm_route_geometry(waypoints)
            leg_times = info.get("leg_durations_min") or []
            if len(leg_times) != len(sequence):
                raise RuntimeError("OSRM did not return a travel time for every route leg; no route was published.")
            total_dwell = sum(_stop_dwell_minutes(idx, nodes) for idx in sequence)
            ride = round(sum(leg_times) + total_dwell, 1)
            route_cache[key] = (info, ride)
        return route_cache[key]

    def split(sequence: List[int]) -> Tuple[List[Tuple[List[int], Dict[str, Any]]], List[int]]:
        try:
            info, ride = evaluate(sequence)
        except UnreachableRoadRouteError:
            if len(sequence) == 1:
                return [], sequence
            options = []
            for cut in range(1, len(sequence)):
                left_routes, left_outliers = split(sequence[:cut])
                right_routes, right_outliers = split(sequence[cut:])
                candidate_routes = left_routes + right_routes
                candidate_outliers = left_outliers + right_outliers
                max_duration = max(
                    (sum(route_info.get("leg_durations_min", [])) + sum(
                        _stop_dwell_minutes(idx, nodes) for idx in route
                    ) for route, route_info in candidate_routes),
                    default=0,
                )
                options.append((
                    len(candidate_outliers), len(candidate_routes), max_duration,
                    candidate_routes, candidate_outliers,
                ))
            best = min(options, key=lambda item: item[:3])
            return best[3], best[4]
        if ride <= MAX_RIDE_TIME_MIN:
            return [(sequence, info)], []
        if len(sequence) == 1:
            return [], sequence

        candidates = []
        for cut in range(1, len(sequence)):
            left, right = sequence[:cut], sequence[cut:]
            left_info, left_ride = evaluate(left)
            right_info, right_ride = evaluate(right)
            candidates.append((
                max(left_ride, right_ride),
                left_ride + right_ride,
                abs(len(left) - len(right)),
                left,
                right,
                left_info,
                right_info,
            ))

        feasible_splits = [candidate for candidate in candidates if candidate[0] <= MAX_RIDE_TIME_MIN]
        if feasible_splits:
            best = min(feasible_splits, key=lambda candidate: candidate[:3])
            return [(best[3], best[5]), (best[4], best[6])], []

        # No two-part split satisfies the cap yet. Split at the point that
        # most reduces the worst route, then recursively validate both halves.
        best = min(candidates, key=lambda candidate: candidate[:3])
        left_routes, left_outliers = split(best[3])
        right_routes, right_outliers = split(best[4])
        return left_routes + right_routes, left_outliers + right_outliers

    return split(stop_indices)


def improve_validated_route_sequences(
    route_sequences: List[List[int]],
    nodes: List[Dict[str, Any]],
    time_matrix: List[List[int]],
    plant_node: int,
) -> List[List[List[int]]]:
    """Return ranked local-search candidates seeded from distinct route orders.

    The OR-Tools solver works on the full road-time matrix. This second pass
    starts from the routes that have already passed OSRM geometry validation,
    so any changes made after validation are checked again before publication.
    """
    from functools import lru_cache

    base = [list(route) for route in route_sequences if route]
    if not base:
        return []

    @lru_cache(maxsize=100_000)
    def duration(sequence: Tuple[int, ...]) -> int:
        if not sequence:
            return 0
        travel = sum(time_matrix[a][b] for a, b in zip(sequence, sequence[1:]))
        travel += time_matrix[sequence[-1]][plant_node]
        dwell = sum(math.ceil(_stop_dwell_minutes(idx, nodes)) for idx in sequence)
        return int(travel + dwell)

    @lru_cache(maxsize=100_000)
    def demand(sequence: Tuple[int, ...]) -> int:
        return sum(int(nodes[i]["demand"]) for i in sequence)

    def score(routes: List[List[int]]) -> Tuple[int, int, int]:
        durations = [duration(tuple(route)) for route in routes]
        # Keep the same priority as the primary optimizer: fleet size first,
        # then bring routes toward 60 minutes, then reduce total road time.
        target_penalty = sum(max(0, d - int(TARGET_RIDE_TIME_MIN)) * 300 for d in durations)
        return len(routes), target_penalty, sum(durations)

    def feasible(sequence: List[int]) -> bool:
        key = tuple(sequence)
        return demand(key) <= BUS_CAPACITY and duration(key) <= MAX_RIDE_TIME_MIN

    # Multiple deterministic starting orders help escape a poor initial stop
    # sequence without making route output vary randomly between runs.
    starts = [base]
    starts.append([list(reversed(route)) for route in base])
    starts.append([
        sorted(route, key=lambda i: (time_matrix[i][plant_node], nodes[i]["name"]), reverse=True)
        for route in base
    ])

    candidate_plans: Dict[Tuple[Tuple[int, ...], ...], List[List[int]]] = {}
    for start in starts:
        current = [list(route) for route in start]
        for _ in range(max(5, min(30, len(nodes) * 2))):
            current_score = score(current)
            best_candidate = None
            best_score = current_score

            # Reorder stops within each bus route (2-opt).
            for r_idx, route in enumerate(current):
                for left in range(len(route) - 1):
                    for right in range(left + 1, len(route)):
                        candidate = [list(r) for r in current]
                        candidate[r_idx][left:right + 1] = reversed(candidate[r_idx][left:right + 1])
                        if not feasible(candidate[r_idx]):
                            continue
                        candidate_score = score(candidate)
                        if candidate_score < best_score:
                            best_candidate, best_score = candidate, candidate_score

            # Relocate a pickup to another position or another bus. A move that
            # empties a route removes that bus, matching the primary objective.
            for source_idx, source_route in enumerate(current):
                for stop_pos, stop in enumerate(source_route):
                    reduced = source_route[:stop_pos] + source_route[stop_pos + 1:]
                    for target_idx, target_route in enumerate(current):
                        if target_idx == source_idx:
                            for insert_pos in range(len(reduced) + 1):
                                candidate_route = reduced[:insert_pos] + [stop] + reduced[insert_pos:]
                                if candidate_route == source_route or not feasible(candidate_route):
                                    continue
                                candidate = [list(r) for r in current]
                                candidate[source_idx] = candidate_route
                                candidate_score = score(candidate)
                                if candidate_score < best_score:
                                    best_candidate, best_score = candidate, candidate_score
                            continue

                        if demand(tuple(target_route)) + int(nodes[stop]["demand"]) > BUS_CAPACITY:
                            continue
                        for insert_pos in range(len(target_route) + 1):
                            candidate_target = target_route[:insert_pos] + [stop] + target_route[insert_pos:]
                            if not feasible(candidate_target) or (reduced and not feasible(reduced)):
                                continue
                            candidate = [list(r) for r in current]
                            candidate[target_idx] = candidate_target
                            if reduced:
                                candidate[source_idx] = reduced
                            else:
                                candidate.pop(source_idx)
                            candidate_score = score(candidate)
                            if candidate_score < best_score:
                                best_candidate, best_score = candidate, candidate_score

            # A one-way move can be blocked when the receiving route is near
            # its 70-minute limit, even though exchanging stops would make
            # both buses shorter. Try every feasible one-for-one cross-bus
            # swap so a pickup close to another bus's corridor can move there
            # without leaving an expensive pickup behind on the source route.
            for left_idx in range(len(current)):
                for right_idx in range(left_idx + 1, len(current)):
                    left_route = current[left_idx]
                    right_route = current[right_idx]
                    for left_pos, left_stop in enumerate(left_route):
                        for right_pos, right_stop in enumerate(right_route):
                            left_candidate = list(left_route)
                            right_candidate = list(right_route)
                            left_candidate[left_pos] = right_stop
                            right_candidate[right_pos] = left_stop
                            if not feasible(left_candidate) or not feasible(right_candidate):
                                continue
                            candidate = [list(route) for route in current]
                            candidate[left_idx] = left_candidate
                            candidate[right_idx] = right_candidate
                            candidate_score = score(candidate)
                            if candidate_score < best_score:
                                best_candidate, best_score = candidate, candidate_score

            # Try to retire a complete bus by inserting all its stops into the
            # remaining routes. This is an atomic move, so intermediate steps
            # cannot block a fleet reduction merely because they add minutes.
            for source_idx, source_route in enumerate(current):
                if len(current) <= 1:
                    break
                remaining = [list(r) for i, r in enumerate(current) if i != source_idx]
                for ordered_stops in (source_route, sorted(source_route, key=lambda i: int(nodes[i]["demand"]), reverse=True)):
                    candidate_routes = [list(r) for r in remaining]
                    possible = True
                    for stop in ordered_stops:
                        insertions = []
                        for target_idx, target_route in enumerate(candidate_routes):
                            if demand(tuple(target_route)) + int(nodes[stop]["demand"]) > BUS_CAPACITY:
                                continue
                            for insert_pos in range(len(target_route) + 1):
                                inserted = target_route[:insert_pos] + [stop] + target_route[insert_pos:]
                                if feasible(inserted):
                                    delta = duration(tuple(inserted)) - duration(tuple(target_route))
                                    insertions.append((delta, target_idx, insert_pos, inserted))
                        if not insertions:
                            possible = False
                            break
                        _, target_idx, _, inserted = min(insertions, key=lambda item: (item[0], item[1], item[2]))
                        candidate_routes[target_idx] = inserted
                    if possible:
                        candidate_score = score(candidate_routes)
                        if candidate_score < best_score:
                            best_candidate, best_score = candidate_routes, candidate_score

            if best_candidate is None:
                break
            current = best_candidate

        key = tuple(tuple(route) for route in current if route)
        candidate_plans[key] = [list(route) for route in current if route]

    # Keep every distinct seed result so the caller can validate each against
    # actual OSRM route legs and choose the best feasible plan, not just the
    # best score from the approximate table matrix.
    return sorted(candidate_plans.values(), key=score)


def solve_shift_routes(
    shift: str,
    employees: List[Dict[str, Any]],
    stop_lookup: Dict[str, Any],
    facility: Dict[str, Any],
    resolution_details: Optional[Dict[str, Dict[str, Any]]] = None,
    shift_deadlines: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Solves the multi-bus CVRPTW for a single shift using Google OR-Tools."""
    cfg = dict(SHIFT_CONFIG[shift])
    if shift_deadlines and shift in shift_deadlines:
        cfg["deadline_min"] = int(shift_deadlines[shift])
        cfg["deadline"] = _display_clock(cfg["deadline_min"])
        cfg["in_time"] = _display_clock(cfg["deadline_min"] + config.COMMUTE.arrival_buffer_minutes)
        cfg["display_deadline"] = _display_ampm(cfg["deadline_min"])
    deadline_min = cfg["deadline_min"]

    # 1. Map employees to verified physical coordinates
    stop_groups: Dict[str, Dict[str, Any]] = {}
    outliers = []

    for emp in employees:
        raw_stop = str(emp.get("raw_stop", "")).strip()
        if not raw_stop or raw_stop.lower() == "nan":
            outliers.append({**emp, "reason": "No pickup stop provided in roster"})
            continue

        key = _stop_key(raw_stop)
        loc = stop_lookup.get(key)
        if not loc:
            resolution = (resolution_details or {}).get(raw_stop, {})
            reason = resolution.get("resolution_reason") or "Pickup stop could not be matched to a verified location."
            outliers.append({**emp, "reason": reason})
            continue

        lat = loc["lat"]
        lon = loc["lon"]
        location_status, location_reason = evaluate_coordinate_candidate(lat, lon, facility=facility)
        if location_status != "VERIFIED":
            outliers.append({
                **emp,
                "reason": location_reason or "Pickup coordinate is outside the confirmed service area.",
            })
            continue
        coord_key = f"{round(lat, 6)}_{round(lon, 6)}"
        if coord_key not in stop_groups:
            stop_groups[coord_key] = {
                "name": loc["name"],
                "lat": lat,
                "lon": lon,
                "employees": []
            }
        stop_groups[coord_key]["employees"].append(emp)

    # 2. Merge compact, capacity-safe pickup clusters independent of roster row order.
    merged_stops = merge_nearby_pickups(stop_groups)

    num_pickups = len(merged_stops)
    total_demand = sum(s["demand"] for s in merged_stops)

    if num_pickups == 0:
        return {
            "shift": shift, "routes": [], "buses_used": 0, "avg_occupancy_pct": 0.0,
            "total_distance_km": 0.0, "longest_ride_time_min": 0.0,
            "total_employees_in_roster": len(employees), "assigned_passengers": 0,
            "outliers": outliers,
            "checks": {
                "passengers_assigned_match": "PASS" if len(outliers) == len(employees) else "FAIL",
                "capacity_under_32": "PASS",
                "ride_time_under_70": "PASS",
            },
        }

    # 3. Nodes:
    # 0 .. num_pickups - 1 : Pickup stops
    # num_pickups : Plant (Drop-off point / Destination)
    # num_pickups + 1 : Virtual Start node (Allows each bus to start at its first stop with zero initial cost)
    nodes = list(merged_stops)
    plant_node = num_pickups
    start_node = num_pickups + 1

    nodes.append({
        "name": facility["name"],
        "lat": facility["lat"],
        "lon": facility["lon"],
        "demand": 0,
        "employees": []
    })
    nodes.append({
        "name": "Virtual Depot Start",
        "lat": facility["lat"],
        "lon": facility["lon"],
        "demand": 0,
        "employees": []
    })

    num_nodes = len(nodes)
    raw_time_matrix, dist_matrix = get_road_matrix(nodes[:plant_node + 1])

    # Extend time matrix to include virtual start node (time = 0 from virtual start to any node)
    time_matrix = [[0] * num_nodes for _ in range(num_nodes)]
    for i in range(plant_node + 1):
        for j in range(plant_node + 1):
            time_matrix[i][j] = raw_time_matrix[i][j]

    # Offer one vehicle per pickup cluster so a fixed upper cap cannot force
    # employees out of the plan when additional buses would make them feasible.
    num_vehicles = num_pickups

    starts = [start_node] * num_vehicles
    ends = [plant_node] * num_vehicles

    manager = pywrapcp.RoutingIndexManager(num_nodes, num_vehicles, starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    # The objective uses road travel time only; no artificial corridor penalty.
    def time_callback(from_index, to_index):
        u = manager.IndexToNode(from_index)
        v = manager.IndexToNode(to_index)
        if u == start_node:
            return 0
        dwell = int(math.ceil(_stop_dwell_minutes(u, nodes))) if u < num_pickups else 0

        return time_matrix[u][v] + dwell

    transit_callback_index = routing.RegisterTransitCallback(time_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Lexicographic objective weights: serve passengers first, then minimize
    # buses, then favor the 60-minute target and minimize road minutes. Bounds
    # are derived from this roster's matrix, not tuned as magic constants.
    max_leg_minutes = max((max(row) for row in time_matrix), default=0)
    max_dwell = sum(math.ceil(_stop_dwell_minutes(idx, nodes)) for idx in range(num_pickups))
    max_total_minutes = max(1, (num_pickups + num_vehicles) * max_leg_minutes + max_dwell)
    max_target_penalty = num_vehicles * max(0, int(MAX_RIDE_TIME_MIN - TARGET_RIDE_TIME_MIN)) * 300
    max_total_cost = max_total_minutes + max_target_penalty
    fixed_bus_cost = max_total_cost + 1
    unserved_penalty = num_vehicles * fixed_bus_cost + max_total_cost + 1
    routing.SetFixedCostOfAllVehicles(fixed_bus_cost)

    # Capacity Dimension (Hard constraint: max 32 passengers)
    def demand_callback(from_index):
        u = manager.IndexToNode(from_index)
        return nodes[u]["demand"]

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,
        [BUS_CAPACITY] * num_vehicles,
        True,
        "Capacity"
    )

    # Route duration has a hard 70-minute ceiling and a soft 60-minute target.
    routing.AddDimension(
        transit_callback_index,
        180,
        180,
        True,
        "Time"
    )
    time_dimension = routing.GetDimensionOrDie("Time")
    for v in range(num_vehicles):
        end_idx = routing.End(v)
        time_dimension.CumulVar(end_idx).SetRange(0, int(MAX_RIDE_TIME_MIN))
        time_dimension.SetCumulVarSoftUpperBound(end_idx, int(TARGET_RIDE_TIME_MIN), 300)

    # Allow the solver to leave a stop unassigned only when no feasible bus can
    # serve it within capacity and the hard ride-time limit. Such employees are
    # reported as outliers below instead of silently breaking the time ceiling.
    for node_idx in range(num_pickups):
        routing.AddDisjunction(
            [manager.NodeToIndex(node_idx)],
            unserved_penalty * max(1, int(nodes[node_idx]["demand"])),
        )

    # Search parameters with guided local search
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    # Give the guided local search enough room to improve the first feasible
    # allocation before returning the plan (per shift).
    search_parameters.time_limit.seconds = SOLVER_TIME_LIMIT_SECONDS

    solution = routing.SolveWithParameters(search_parameters)

    routes_output = []
    buses_used = 0
    assigned_passengers = 0
    total_shift_km = 0.0

    if solution:
        solver_routes = []
        for vehicle_id in range(num_vehicles):
            index = routing.Start(vehicle_id)
            route_stops = []

            while not routing.IsEnd(index):
                node_idx = manager.IndexToNode(index)
                if node_idx < num_pickups:
                    route_stops.append(node_idx)
                index = solution.Value(routing.NextVar(index))

            if route_stops:
                solver_routes.append(route_stops)

        validated_routes = []
        impossible_nodes = set()
        for route_stops in solver_routes:
            repaired_routes, impossible_stops = repair_route_sequences(route_stops, nodes, facility)
            validated_routes.extend(repaired_routes)
            impossible_nodes.update(impossible_stops)

        def actual_plan_score(candidate_routes):
            covered = {node_idx for sequence, _ in candidate_routes for node_idx in sequence}
            served = sum(int(nodes[idx]["demand"]) for idx in covered)
            durations, distance = [], 0.0
            for sequence, road_info in candidate_routes:
                ride = sum(road_info.get("leg_durations_min", [])) + sum(
                    _stop_dwell_minutes(idx, nodes) for idx in sequence
                )
                durations.append(ride)
                distance += float(road_info.get("distance_km") or sum(road_info.get("leg_distances_km", [])))
            target_excess = sum(max(0.0, ride - TARGET_RIDE_TIME_MIN) for ride in durations)
            return (-served, len(candidate_routes), round(target_excess, 2), round(sum(durations), 2), round(distance, 2))

        # Re-optimize after any OSRM-driven split. Check several deterministic
        # alternatives against actual road legs and keep the best feasible one.
        for _ in range(2):
            current_sequences = [sequence for sequence, _ in validated_routes]
            candidate_plans = improve_validated_route_sequences(
                current_sequences, nodes, time_matrix, plant_node
            )
            best_routes, best_impossible = validated_routes, impossible_nodes
            best_score = actual_plan_score(best_routes)
            for plan in candidate_plans:
                checked_routes, checked_impossible = [], set()
                for route_stops in plan:
                    repaired, impossible = repair_route_sequences(route_stops, nodes, facility)
                    checked_routes.extend(repaired)
                    checked_impossible.update(impossible)
                score = actual_plan_score(checked_routes)
                if score < best_score:
                    best_routes, best_impossible, best_score = checked_routes, checked_impossible, score
            if [tuple(route) for route, _ in best_routes] == [tuple(route) for route, _ in validated_routes]:
                break
            validated_routes, impossible_nodes = best_routes, best_impossible

        for route_stops, road_info in validated_routes:
            route_demand = sum(nodes[node_idx]["demand"] for node_idx in route_stops)
            buses_used += 1
            assigned_passengers += route_demand
            leg_durations = road_info["leg_durations_min"]
            leg_distances = road_info["leg_distances_km"]
            drive_time_min = round(sum(leg_durations), 1)
            route_dwells = [_stop_dwell_minutes(node_idx, nodes) for node_idx in route_stops]
            route_duration_min = round(drive_time_min + sum(route_dwells), 1)
            if route_duration_min > MAX_RIDE_TIME_MIN:
                raise RuntimeError(
                    f"A mapped route exceeded the {MAX_RIDE_TIME_MIN:g}-minute limit after repair; no plan was published."
                )

            detailed_stops = []
            for stop_idx, node_idx in enumerate(route_stops):
                pickup_min = deadline_min - sum(leg_durations[stop_idx:]) - sum(route_dwells[stop_idx:])
                p_min = int(round(pickup_min))
                p_hour = (p_min // 60) % 24
                p_rem = p_min % 60
                period = "AM" if p_hour < 12 else "PM"
                display_hour = p_hour % 12 or 12
                detailed_stops.append({
                    "stop_name": nodes[node_idx]["name"],
                    "lat": nodes[node_idx]["lat"],
                    "lon": nodes[node_idx]["lon"],
                    "demand": nodes[node_idx]["demand"],
                    "scheduled_time": f"{display_hour:02d}:{p_rem:02d} {period}",
                    "employees": nodes[node_idx]["employees"],
                })

            route_dist_km = float(road_info["distance_km"] or sum(leg_distances))
            total_shift_km += route_dist_km
            corridor_label = build_corridor_label([nodes[node_idx]["name"] for node_idx in route_stops])
            actual_geometry = road_info.get("coordinates")
            if not actual_geometry or len(actual_geometry) < 2:
                raise RuntimeError("OSRM did not return mapped geometry; no route was published.")

            routes_output.append({
                "route_no": buses_used,
                "corridor_name": corridor_label,
                "total_passengers": route_demand,
                "occupancy_pct": round((route_demand / BUS_CAPACITY) * 100.0, 1),
                "total_distance_km": round(route_dist_km, 1),
                "route_duration_min": route_duration_min,
                "drive_time_min": drive_time_min,
                "duration_source": "OSRM road estimate plus configured stop dwell",
                "leg_distances_km": leg_distances,
                "first_pickup_time": detailed_stops[0]["scheduled_time"],
                "plant_arrival_time": cfg["display_deadline"],
                "road_geometry": actual_geometry,
                "ride_time_status": (
                    "ON_TARGET" if route_duration_min <= TARGET_RIDE_TIME_MIN else "WITHIN_ALLOWANCE"
                ),
                "stops": detailed_stops,
            })
        assigned_node_ids = {node_idx for route, _ in validated_routes for node_idx in route}
        for node_idx, node in enumerate(nodes[:num_pickups]):
            if node_idx in assigned_node_ids:
                continue
            if node_idx in impossible_nodes:
                reason = f"Mapped road travel from this pickup exceeds the {MAX_RIDE_TIME_MIN:g}-minute ride limit."
            else:
                reason = "No feasible bus allocation was found under the current capacity and ride-time limits."
            for emp in node["employees"]:
                outliers.append({**emp, "reason": reason})
    else:
        for stop in merged_stops:
            for emp in stop["employees"]:
                outliers.append({**emp, "reason": "No feasible mapped-road route was found for this shift."})

    avg_occupancy = round((assigned_passengers / (buses_used * BUS_CAPACITY) * 100.0), 1) if buses_used > 0 else 0.0
    longest_ride = max([r["route_duration_min"] for r in routes_output], default=0.0)

    return {
        "shift": shift,
        "shift_start": cfg["in_time"],
        "plant_deadline": cfg["display_deadline"],
        "total_employees_in_roster": len(employees),
        "assigned_passengers": assigned_passengers,
        "buses_used": buses_used,
        "avg_occupancy_pct": avg_occupancy,
        "total_distance_km": round(total_shift_km, 1),
        "longest_ride_time_min": longest_ride,
        "routes": routes_output,
        "outliers": outliers,
        "checks": {
            "passengers_assigned_match": "PASS" if assigned_passengers == len(employees) - len(outliers) else "FAIL",
            "capacity_under_32": "PASS" if all(r["total_passengers"] <= BUS_CAPACITY for r in routes_output) else "FAIL",
            "ride_time_under_70": "PASS" if longest_ride <= MAX_RIDE_TIME_MIN else "FAIL",
        },
        "optimization": {
            "solver": "OR-Tools Guided Local Search",
            "road_data_source": "OSRM",
            "objective_order": [
                "maximize employees served",
                "minimize buses",
                f"favor {TARGET_RIDE_TIME_MIN:g}-minute target",
                "minimize road travel time",
            ],
            "search_limit_seconds": SOLVER_TIME_LIMIT_SECONDS,
            "post_validation_improvement": "Deterministic 2-opt, cross-route stop relocation and swaps, and feasible bus-retirement search; changed routes revalidated on OSRM",
            "final_route_validation": "OSRM geometry and per-leg durations",
            "candidate_selection": "Best feasible among deterministic local-search candidates validated with OSRM; this is a heuristic, not a proof of global optimality",
            "eta_method": "Scheduled backward from the configured plant deadline using OSRM estimated road times and configured stop dwell; live traffic is not included",
            "configured_stop_dwell_minutes": DWELL_TIME_MIN,
            "configured_boarding_seconds_per_passenger": BOARDING_SECONDS_PER_PASSENGER,
        },
    }


def generate_transport_roster_workbook(
    all_shift_results: List[Dict[str, Any]],
    output_path: Path,
    raw_df: Optional[pd.DataFrame] = None,
    facility: Optional[Dict[str, Any]] = None,
):
    """Generates the formatted Excel workbook with separate sheets for each shift,
    plus Raw Data and Shift Summary sheets, strictly matching supervisor specifications."""
    output_path = Path(output_path)
    wb = Workbook()
    wb.remove(wb.active)  # remove default sheet

    # Color Palette matching User Images
    gold_fill = PatternFill(start_color="FFC000", end_color="FFC000", fill_type="solid")
    gold_header_font = Font(name="Calibri", size=11, bold=True, color="000000")
    title_font = Font(name="Calibri", size=13, bold=True, color="000000")
    sub_title_font = Font(name="Calibri", size=11, bold=True, color="000000")
    body_font = Font(name="Calibri", size=10, color="000000")
    border_thin = Border(
        left=Side(style='thin', color='B2B2B2'),
        right=Side(style='thin', color='B2B2B2'),
        top=Side(style='thin', color='B2B2B2'),
        bottom=Side(style='thin', color='B2B2B2')
    )

    headers = [
        "Sr. No.", "Employee Type", "Employee Name", "Shift", "Route Name",
        "Pickup point", "Mobile No.", "Route No.", "Vehicle Required", "Driver Name",
        "Driver Contact", "Pickup Time"
    ]

    col_widths = {
        "A": 8, "B": 15, "C": 22, "D": 10, "E": 34, "F": 34,
        "G": 15, "H": 11, "I": 18, "J": 16, "K": 16, "L": 15
    }

    # ----------------------------------------------------
    # Sheets 1, 2, 3: Separate sheet for EACH SHIFT (Shift A, Shift B, Shift C)
    # ----------------------------------------------------
    facility = facility or {}
    facility_name = str(facility.get("name") or "").strip()
    try:
        facility_lat = float(facility["lat"])
        facility_lon = float(facility["lon"])
        facility_coordinates = f"{facility_lat:.6f}, {facility_lon:.6f}"
    except (KeyError, TypeError, ValueError):
        facility_coordinates = ""
    dropoff_label = " — ".join(part for part in (facility_name, facility_coordinates) if part)
    if not dropoff_label:
        dropoff_label = "Not provided"

    for shift_res in all_shift_results:
        shift_name = shift_res["shift"]
        ws = wb.create_sheet(title=f"Shift {shift_name}")

        # Title Block (Rows 1-3)
        ws.merge_cells("A1:L1")
        ws["A1"] = f"Roster Sheet — Shift {shift_name}"
        ws["A1"].font = title_font
        ws["A1"].fill = gold_fill
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells("A2:L2")
        ws["A2"] = f"Drop-off Location: {dropoff_label}"
        ws["A2"].font = sub_title_font
        ws["A2"].fill = gold_fill
        ws["A2"].alignment = Alignment(horizontal="center", vertical="center")

        ws.merge_cells("A3:L3")
        ws["A3"] = "Supervisor Details: ______________________________"
        ws["A3"].font = sub_title_font
        ws["A3"].fill = gold_fill
        ws["A3"].alignment = Alignment(horizontal="center", vertical="center")

        for r in range(1, 4):
            ws.row_dimensions[r].height = 24

        current_row = 4
        sr_counter = 1

        routes = shift_res.get("routes", [])
        for route in routes:
            veh_no = f"{BUS_CAPACITY}-Seater Bus"
            driver_name = ""  # Left empty for supervisor to assign daily driver
            driver_mob = ""
            route_num = route["route_no"]
            corridor = route["corridor_name"]

            # Write Gold Route Subheader (Row matching Image 2 & 3)
            ws.row_dimensions[current_row].height = 20
            for col_idx, h_text in enumerate(headers, 1):
                cell = ws.cell(row=current_row, column=col_idx, value=h_text)
                cell.font = gold_header_font
                cell.fill = gold_fill
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.border = border_thin
            current_row += 1

            # Write Employee rows under this route
            for stop in route["stops"]:
                stop_name = stop["stop_name"]
                sched_time = stop["scheduled_time"]

                for emp in stop["employees"]:
                    ws.row_dimensions[current_row].height = 19
                    row_vals = [
                        sr_counter,
                        emp.get("emp_type", "DET"),
                        emp.get("emp_name", ""),
                        emp.get("shift", shift_name),
                        corridor,
                        stop_name,
                        emp.get("mobile", ""),
                        route_num,
                        veh_no,
                        driver_name,
                        driver_mob,
                        sched_time
                    ]

                    for col_idx, val in enumerate(row_vals, 1):
                        cell = ws.cell(row=current_row, column=col_idx, value=val)
                        cell.font = body_font
                        cell.border = border_thin
                        if col_idx in [1, 4, 8, 12]:
                            cell.alignment = Alignment(horizontal="center", vertical="center")
                        elif col_idx in [7, 9, 11]:
                            cell.alignment = Alignment(horizontal="center", vertical="center")
                        else:
                            cell.alignment = Alignment(horizontal="left", vertical="center")

                    sr_counter += 1
                    current_row += 1

        for col_letter, width in col_widths.items():
            ws.column_dimensions[col_letter].width = width

    # ----------------------------------------------------
    # Sheet: "Raw Data" (Original Supervisor input roster)
    # ----------------------------------------------------
    ws_raw = wb.create_sheet(title="Raw Data")
    header_navy = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    font_white_bold = Font(name="Calibri", size=11, bold=True, color="FFFFFF")

    if raw_df is not None and not raw_df.empty:
        raw_headers = list(raw_df.columns)
        ws_raw.row_dimensions[1].height = 24
        for col_idx, col_name in enumerate(raw_headers, 1):
            cell = ws_raw.cell(row=1, column=col_idx, value=str(col_name))
            cell.font = font_white_bold
            cell.fill = header_navy
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border_thin

        for row_idx, r in raw_df.iterrows():
            ws_raw.row_dimensions[row_idx + 2].height = 19
            for col_idx, col_name in enumerate(raw_headers, 1):
                val = r[col_name]
                if pd.isna(val):
                    val = ""
                cell = ws_raw.cell(row=row_idx + 2, column=col_idx, value=str(val) if not isinstance(val, (int, float)) else val)
                cell.font = body_font
                cell.border = border_thin
                cell.alignment = Alignment(horizontal="left", vertical="center")

        for col in ws_raw.columns:
            max_len = max([len(str(c.value or "")) for c in col], default=15)
            col_letter = col[0].column_letter
            ws_raw.column_dimensions[col_letter].width = max(14, min(max_len + 4, 38))

    # ----------------------------------------------------
    # Sheet 5: "Needs Attention" (Outliers / Missing Pickup Points)
    # ----------------------------------------------------
    ws_out = wb.create_sheet(title="Needs Attention")
    out_headers = ["Employee Name", "Shift", "Pickup Point", "Mobile No.", "Reason Flagged", "Action Required"]
    ws_out.append(out_headers)
    ws_out.row_dimensions[1].height = 24
    for cell in ws_out[1]:
        cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="78281F", end_color="78281F", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    total_outliers = 0
    for res in all_shift_results:
        for out in res["outliers"]:
            total_outliers += 1
            ws_out.append([
                out.get("emp_name", ""),
                out.get("shift", ""),
                out.get("raw_stop", ""),
                out.get("mobile", ""),
                out.get("reason", ""),
                "Supervisor to verify address or assign cab / alternative shift"
            ])

    if total_outliers == 0:
        ws_out.append(["None", "None", "None", "None", "All employees successfully routed!", "No Action Needed"])

    for row in ws_out.iter_rows(min_row=2, max_row=max(2, total_outliers + 1)):
        for cell in row:
            cell.font = body_font
            cell.border = border_thin

    for col in ws_out.columns:
        ws_out.column_dimensions[col[0].column_letter].width = 25

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    print(f"\n[Workbook Generated] Saved full multi-shift transport roster to:\n  {output_path}")


def run_full_pipeline(
    roster_excel_path: Path,
    output_excel_path: Path,
    facility: Optional[Dict[str, Any]] = None,
    pickup_resolver=None,
    shift_deadlines: Optional[Dict[str, int]] = None,
):
    print("=" * 70)
    print("STARTING MULTI-SHIFT VRP ROUTE PLANNING PIPELINE")
    print(f"Roster File: {roster_excel_path}")
    print(f"Output File: {output_excel_path}")
    print("=" * 70)

    # The caller must supply this supervisor's currently confirmed facility.
    stop_lookup = load_all_verified_stops()
    if not facility or facility.get("lat") is None or facility.get("lon") is None:
        raise ValueError("Choose and confirm a drop-off facility before planning routes.")

    # Read roster Excel
    df = pd.read_excel(roster_excel_path, header=1)
    df = df.dropna(how="all").reset_index(drop=True)

    # Resolve unique roster stop labels missing from the verified stop cache.
    # The resolver uses Geoapify first and LocationIQ as a cross-check/fallback.
    resolution_details: Dict[str, Dict[str, Any]] = {}
    if pickup_resolver:
        raw_names = list(dict.fromkeys(
            str(value).strip()
            for value in df.get("Bus Stop", pd.Series(dtype=str)).tolist()
            if str(value).strip() and str(value).strip().lower() != "nan"
        ))
        unmatched_names = []
        for name in raw_names:
            master_location = _master_match(name, stop_lookup)
            if master_location:
                status, _ = evaluate_coordinate_candidate(
                    master_location.get("lat"), master_location.get("lon"), facility=facility
                )
                if status == "VERIFIED":
                    stop_lookup[_stop_key(name)] = master_location
                    continue
            unmatched_names.append(name)
        if unmatched_names:
            resolution_details = pickup_resolver(unmatched_names, facility)
            for raw_name, resolution in resolution_details.items():
                if resolution.get("location_status") != "VERIFIED":
                    continue
                try:
                    item = {
                        "name": resolution.get("canonical_name") or raw_name,
                        "lat": float(resolution["latitude"]),
                        "lon": float(resolution["longitude"]),
                    }
                except (KeyError, TypeError, ValueError):
                    continue
                stop_lookup[_stop_key(raw_name)] = item
                stop_lookup[_stop_key(str(item["name"]))] = item
            print(
                f"[Geocoding] Resolved {sum(r.get('location_status') == 'VERIFIED' for r in resolution_details.values())} "
                f"of {len(unmatched_names)} previously unknown pickup labels."
            )

    all_employees = []
    for idx, r in df.iterrows():
        all_employees.append({
            "sr_no": idx + 1,
            "emp_type": str(r.get("GRADE", "DET")).strip(),
            "emp_name": str(r.get("Name", f"Emp_{idx+1}")).strip(),
            "shift": str(r.get("Shift", "A")).strip(),
            "raw_stop": str(r.get("Bus Stop", "")).strip(),
            "mobile": str(r.get("Mob. No.", "")).replace(".0", "").strip(),
        })

    def map_primary_shift(s_val: str) -> str:
        s = str(s_val).upper()
        if "A" in s: return "A"
        if "B" in s: return "B"
        if "C" in s: return "C"
        return "A"

    all_results = []
    for shift_key in ["A", "B", "C"]:
        shift_emps = [e for e in all_employees if map_primary_shift(e["shift"]) == shift_key]
        res = solve_shift_routes(
            shift_key,
            shift_emps,
            stop_lookup,
            facility,
            resolution_details,
            shift_deadlines,
        )
        all_results.append(res)

    # Generate Excel with separate sheets for each shift + Raw Data
    generate_transport_roster_workbook(all_results, output_excel_path, raw_df=df, facility=facility)
    return all_results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Plan multi-shift employee bus routes from an Excel roster.")
    parser.add_argument("roster", type=Path, help="Path to the supervisor-provided Excel roster")
    parser.add_argument("output", type=Path, help="Path for the generated route workbook")
    parser.add_argument("--facility-name", required=True, help="Name from the supervisor-confirmed drop-off")
    parser.add_argument("--facility-lat", required=True, type=float, help="Latitude from the confirmed drop-off")
    parser.add_argument("--facility-lon", required=True, type=float, help="Longitude from the confirmed drop-off")
    args = parser.parse_args()
    from backend.app.etl.transform.coordinate_resolver import resolve_roster_pickup_coordinates

    run_full_pipeline(
        args.roster,
        args.output,
        {"name": args.facility_name, "lat": args.facility_lat, "lon": args.facility_lon},
        resolve_roster_pickup_coordinates,
    )
