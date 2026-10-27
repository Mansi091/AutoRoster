"""Capacity- and ride-time-aware bus grouping and stop sequencing.

This module contains only logic. It knows no place names, no facility
coordinates and no shift times: everything arrives as arguments, so a changed
roster or a moved drop point needs no code change.

Definitions
-----------
Unit
    One boarding point for one shift. A boarding point with more passengers
    than bus capacity is split into several units.
Route
    The ordered list of units one bus visits before reaching the facility.
Ride time (per passenger)
    Minutes from the bus reaching the passenger's stop until the bus reaches
    the facility. It includes the boarding dwell at the passenger's own stop.
Plan cost
    Sum over passengers of ride minutes, plus a fixed cost per bus. The fixed
    cost is what makes the planner prefer fewer buses, but never at the price
    of breaking capacity or the ride-time limit.

Algorithm
---------
1. Cheapest feasible insertion: seed a bus with the unit farthest from the
   facility, then keep inserting the unit that adds the fewest passenger-minutes
   while the bus stays within capacity and the ride limit.
2. Local search: relocate single units between buses and merge bus pairs,
   accepting a move only if it lowers the plan cost and keeps every route feasible.
3. Sequencing: each final route is ordered to minimise total passenger ride
   minutes. Exact dynamic programming is used for small routes, 2-opt and or-opt
   for larger ones.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import requests

LOGGER = logging.getLogger(__name__)
_EPS = 1e-9


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoutingParams:
    bus_capacity: int
    max_ride_minutes: float
    bus_fixed_cost_minutes: float
    boarding_seconds_per_passenger: float
    min_boarding_minutes: float
    exact_order_max_units: int = 12


@dataclass
class Unit:
    unit_id: int
    stop_key: Tuple[float, float]
    stop_name: str
    node: int                      # index into the travel matrix
    pax: int
    dwell_min: float
    employees: List[dict] = field(default_factory=list)


@dataclass
class TravelMatrix:
    minutes: List[List[float]]
    km: List[List[float]]
    source: str
    estimated_pairs: int


# ---------------------------------------------------------------------------
# Travel matrix
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _estimated_pair(a: Tuple[float, float], b: Tuple[float, float],
                    road_factor: float, speed_kmph: float) -> Tuple[float, float]:
    km = haversine_km(a[0], a[1], b[0], b[1]) * road_factor
    return km / speed_kmph * 60.0, km


def _osrm_matrix(points, base_url, timeout_s, road_factor, speed_kmph):
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in points)
    url = f"{base_url.rstrip('/')}/table/v1/driving/{coords}"
    resp = requests.get(url, params={"annotations": "duration,distance"}, timeout=timeout_s)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM returned {data.get('code')}: {data.get('message', '')}")

    n = len(points)
    minutes = [[0.0] * n for _ in range(n)]
    km = [[0.0] * n for _ in range(n)]
    estimated = 0
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d, m = data["durations"][i][j], data["distances"][i][j]
            if d is None or m is None:
                # OSRM returns null for pairs it cannot connect. Estimate them and count them.
                minutes[i][j], km[i][j] = _estimated_pair(points[i], points[j], road_factor, speed_kmph)
                estimated += 1
            else:
                minutes[i][j] = d / 60.0
                km[i][j] = m / 1000.0
    return minutes, km, estimated


def build_travel_matrix(points: Sequence[Tuple[float, float]], provider: str, osrm_url: str,
                        road_factor: float, speed_kmph: float, timeout_s: int = 30) -> TravelMatrix:
    """Return road minutes and kilometres between every pair of points.

    provider "osrm" uses the OSRM table API and falls back to a straight-line
    estimate if the request fails. The source is recorded so the report can say
    which numbers were used.
    """
    if provider == "osrm":
        try:
            minutes, km, estimated = _osrm_matrix(points, osrm_url, timeout_s, road_factor, speed_kmph)
            label = "OSRM road network"
            if estimated:
                label += f" ({estimated} unconnected pairs estimated)"
            return TravelMatrix(minutes, km, label, estimated)
        except Exception as exc:  # network, JSON, or server error
            LOGGER.warning("OSRM table request failed (%s). Using straight-line estimate.", exc)
    elif provider != "haversine":
        raise ValueError(f"Unknown travel_time_provider '{provider}' (use 'osrm' or 'haversine').")

    n = len(points)
    minutes = [[0.0] * n for _ in range(n)]
    km = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                minutes[i][j], km[i][j] = _estimated_pair(points[i], points[j], road_factor, speed_kmph)
    return TravelMatrix(minutes, km, "STRAIGHT-LINE ESTIMATE (not road data)", n * (n - 1))


# ---------------------------------------------------------------------------
# Route evaluation
# ---------------------------------------------------------------------------

def evaluate(seq: Sequence[int], units: List[Unit], minutes, fac_node: int):
    """Return (rides, passenger_minutes) for an ordered route.

    rides[k] is the ride time of the passengers boarding at seq[k].
    Each edge's weight (dwell at its start + driving time to the next stop)
    counts for every passenger already on board at that point, so a passenger's
    ride is the sum of the weights from their stop to the facility.
    """
    if not seq:
        return [], 0.0
    k = len(seq)
    weights = []
    for pos, u in enumerate(seq):
        nxt = units[seq[pos + 1]].node if pos + 1 < k else fac_node
        weights.append(units[u].dwell_min + minutes[units[u].node][nxt])
    rides = [0.0] * k
    acc = 0.0
    for pos in range(k - 1, -1, -1):
        acc += weights[pos]
        rides[pos] = acc
    pax_minutes = sum(units[u].pax * rides[pos] for pos, u in enumerate(seq))
    return rides, pax_minutes


def is_feasible(seq: Sequence[int], units: List[Unit], minutes, fac_node: int, p: RoutingParams) -> bool:
    if sum(units[u].pax for u in seq) > p.bus_capacity:
        return False
    rides, _ = evaluate(seq, units, minutes, fac_node)
    return all(r <= p.max_ride_minutes + _EPS for r in rides)


def _route_cost(seq, units, minutes, fac_node, p: RoutingParams) -> float:
    if not seq:
        return 0.0
    return evaluate(seq, units, minutes, fac_node)[1] + p.bus_fixed_cost_minutes


# ---------------------------------------------------------------------------
# Sequencing: best pickup order for a fixed set of units
# ---------------------------------------------------------------------------

def _exact_order(unit_ids: List[int], units, minutes, fac_node: int) -> List[int]:
    """Held-Karp style DP over subsets. Exact for small routes."""
    k = len(unit_ids)
    pax = [units[u].pax for u in unit_ids]
    dwell = [units[u].dwell_min for u in unit_ids]
    node = [units[u].node for u in unit_ids]

    maskpax = [0] * (1 << k)
    for mask in range(1, 1 << k):
        low = mask & -mask
        maskpax[mask] = maskpax[mask ^ low] + pax[low.bit_length() - 1]

    inf = float("inf")
    dp = [[inf] * k for _ in range(1 << k)]
    parent = [[-1] * k for _ in range(1 << k)]
    for j in range(k):
        dp[1 << j][j] = 0.0

    for mask in range(1, 1 << k):
        for last in range(k):
            cur = dp[mask][last]
            if cur == inf:
                continue
            for nxt in range(k):
                if mask >> nxt & 1:
                    continue
                nm = mask | (1 << nxt)
                cost = cur + (dwell[last] + minutes[node[last]][node[nxt]]) * maskpax[mask]
                if cost < dp[nm][nxt]:
                    dp[nm][nxt] = cost
                    parent[nm][nxt] = last

    full = (1 << k) - 1
    best, best_last = inf, -1
    for last in range(k):
        cost = dp[full][last] + (dwell[last] + minutes[node[last]][fac_node]) * maskpax[full]
        if cost < best:
            best, best_last = cost, last

    order, mask, last = [], full, best_last
    while last != -1:
        order.append(unit_ids[last])
        prev = parent[mask][last]
        mask ^= 1 << last
        last = prev
    order.reverse()
    return order


def _local_order(seq: List[int], units, minutes, fac_node: int) -> List[int]:
    """2-opt and or-opt on passenger minutes. Used for routes too big for the DP."""
    def cost(s):
        return evaluate(s, units, minutes, fac_node)[1]

    cur, best = list(seq), cost(seq)
    improved = True
    while improved:
        improved = False
        for i in range(len(cur) - 1):
            for j in range(i + 1, len(cur)):
                cand = cur[:i] + cur[i:j + 1][::-1] + cur[j + 1:]
                c = cost(cand)
                if c < best - _EPS:
                    cur, best, improved = cand, c, True
        for i in range(len(cur)):
            unit = cur[i]
            rest = cur[:i] + cur[i + 1:]
            for pos in range(len(rest) + 1):
                cand = rest[:pos] + [unit] + rest[pos:]
                c = cost(cand)
                if c < best - _EPS:
                    cur, best, improved = cand, c, True
                    break
            if improved:
                break
    return cur


def best_order(unit_ids: List[int], units, minutes, fac_node: int, p: RoutingParams) -> List[int]:
    if len(unit_ids) <= 1:
        return list(unit_ids)
    if len(unit_ids) <= p.exact_order_max_units:
        return _exact_order(list(unit_ids), units, minutes, fac_node)
    return _local_order(list(unit_ids), units, minutes, fac_node)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _build_routes(units, minutes, fac_node: int, p: RoutingParams) -> List[List[int]]:
    unassigned = set(range(len(units)))
    routes: List[List[int]] = []

    while unassigned:
        # Seed with the unit farthest from the facility (by road time).
        seed = max(unassigned, key=lambda u: (minutes[units[u].node][fac_node], -u))
        unassigned.discard(seed)
        route = [seed]
        if not is_feasible(route, units, minutes, fac_node, p):
            # This stop cannot meet the ride limit even alone. Keep it on its own bus
            # and flag it for review rather than dropping the passengers.
            routes.append(route)
            continue

        while True:
            base = evaluate(route, units, minutes, fac_node)[1]
            best = None
            route_pax = sum(units[u].pax for u in route)
            for cand in unassigned:
                if route_pax + units[cand].pax > p.bus_capacity:
                    continue
                for pos in range(len(route) + 1):
                    trial = route[:pos] + [cand] + route[pos:]
                    if not is_feasible(trial, units, minutes, fac_node, p):
                        continue
                    delta = evaluate(trial, units, minutes, fac_node)[1] - base
                    if best is None or delta < best[0]:
                        best = (delta, cand, trial)
            if best is None:
                break
            _, cand, route = best
            unassigned.discard(cand)
        routes.append(route)
    return routes


def _relocate_once(routes, units, minutes, fac_node: int, p: RoutingParams) -> bool:
    cost = lambda s: _route_cost(s, units, minutes, fac_node, p)
    for ri, src in enumerate(routes):
        for unit in src:
            src_wo = [x for x in src if x != unit]
            gain = cost(src) - cost(src_wo)
            best = None
            for rj, dst in enumerate(routes):
                if rj == ri:
                    continue
                base = cost(dst)
                for pos in range(len(dst) + 1):
                    trial = dst[:pos] + [unit] + dst[pos:]
                    if not is_feasible(trial, units, minutes, fac_node, p):
                        continue
                    delta = cost(trial) - base - gain
                    if delta < -1e-6 and (best is None or delta < best[0]):
                        best = (delta, rj, trial)
            if best is not None:
                _, rj, trial = best
                routes[rj] = trial
                routes[ri] = src_wo
                routes[:] = [r for r in routes if r]
                return True
    return False


def _merge_once(routes, units, minutes, fac_node: int, p: RoutingParams) -> bool:
    cost = lambda s: _route_cost(s, units, minutes, fac_node, p)
    for a in range(len(routes)):
        for b in range(a + 1, len(routes)):
            ra, rb = routes[a], routes[b]
            if sum(units[u].pax for u in ra + rb) > p.bus_capacity:
                continue
            candidates = []
            joint = best_order(ra + rb, units, minutes, fac_node, p)
            candidates.append(joint)
            inserted = list(ra)
            for u in sorted(rb, key=lambda x: -minutes[units[x].node][fac_node]):
                options = [inserted[:pos] + [u] + inserted[pos:] for pos in range(len(inserted) + 1)]
                options = [o for o in options if is_feasible(o, units, minutes, fac_node, p)]
                if not options:
                    inserted = None
                    break
                inserted = min(options, key=lambda o: evaluate(o, units, minutes, fac_node)[1])
            if inserted is not None:
                candidates.append(inserted)
            feasible = [c for c in candidates if is_feasible(c, units, minutes, fac_node, p)]
            if not feasible:
                continue
            merged = min(feasible, key=cost)
            if cost(merged) < cost(ra) + cost(rb) - 1e-6:
                routes[a] = merged
                del routes[b]
                return True
    return False


def group_units(units: List[Unit], minutes, fac_node: int, p: RoutingParams,
                max_moves: int = 500) -> List[List[int]]:
    """Return routes as lists of unit ids, each ordered by pickup sequence."""
    if not units:
        return []
    routes = _build_routes(units, minutes, fac_node, p)
    moves = 0
    while moves < max_moves:
        if _relocate_once(routes, units, minutes, fac_node, p) or _merge_once(routes, units, minutes, fac_node, p):
            moves += 1
            continue
        break

    final = []
    for route in routes:
        ordered = best_order(route, units, minutes, fac_node, p)
        # Keep the reordered route only if it is still feasible and no worse.
        if is_feasible(ordered, units, minutes, fac_node, p) and \
                evaluate(ordered, units, minutes, fac_node)[1] <= evaluate(route, units, minutes, fac_node)[1] + _EPS:
            final.append(ordered)
        else:
            final.append(route)
    final.sort(key=lambda r: -evaluate(r, units, minutes, fac_node)[1])
    return final
