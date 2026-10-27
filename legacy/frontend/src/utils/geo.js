/**
 * General Geographic & Route Clustering Utilities
 * Groups employees based on pickup points leading towards the company drop point destination.
 */

import MASTER_COORDS from "./master_coords.json" with { type: "json" };
import { roadKey } from "./roadMatrix.js";

// Exact pickup ordering grows exponentially with stop count; cap the state graph.
const EXACT_DIJKSTRA_STOP_LIMIT = 14;
export const BUS_CAPACITY = 32;
export const TARGET_RIDE_MINUTES = 60;
export const MAX_RIDE_MINUTES = 70;
// Calibrated Pune commuter corridor parameters:
// 1.45x road curvature factor from straight line
// 26 km/h realistic city & highway bus speed with traffic
// 1.5 min passenger boarding dwell time per stop
export const PUNE_ROAD_FACTOR = 1.45;
export const FALLBACK_AVERAGE_SPEED_KMH = 26;
export const DWELL_MINUTES_PER_STOP = 1.5;

const normalizeCoordinateKey = (value) =>
  String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

const isWithinPuneServiceArea = (lat, lon) =>
  lat >= 17.9 && lat <= 19.2 && lon >= 73.1 && lon <= 74.6;

const NORMALIZED_MASTER_COORDS = new Map();
for (const [name, coordinates] of Object.entries(MASTER_COORDS)) {
  const key = normalizeCoordinateKey(name);
  if (!NORMALIZED_MASTER_COORDS.has(key)) {
    NORMALIZED_MASTER_COORDS.set(key, coordinates);
    continue;
  }

  const existing = NORMALIZED_MASTER_COORDS.get(key);
  const sameCoordinates =
    existing &&
    existing[0] === coordinates[0] &&
    existing[1] === coordinates[1];
  if (!sameCoordinates) NORMALIZED_MASTER_COORDS.set(key, null);
}

// Google Maps coordinates for JCB India Ltd.
export const DEFAULT_DESTINATION = {
  name: "JCB India Ltd",
  lat: 18.7765871,
  lon: 73.6947478,
};

export function haversineDistanceKm(lat1, lon1, lat2, lon2) {
  const R = 6371.0;
  const p1 = (lat1 * Math.PI) / 180;
  const p2 = (lat2 * Math.PI) / 180;
  const dp = ((lat2 - lat1) * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const a =
    Math.sin(dp / 2) ** 2 +
    Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/**
 * Classifies a coordinate into a natural highway corridor leading towards the destination.
 */
export function getCorridorId(lat, lon, dest = DEFAULT_DESTINATION) {
  if (lat == null || lon == null) return "Local Vicinity";
  const d = haversineDistanceKm(lat, lon, dest.lat, dest.lon);
  if (d <= 5.5) return "Local Vicinity";

  const dLon = ((lon - dest.lon) * Math.PI) / 180;
  const lat1 = (dest.lat * Math.PI) / 180;
  const lat2 = (lat * Math.PI) / 180;
  const y = Math.sin(dLon) * Math.cos(lat2);
  const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
  const bearing = (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;

  if (bearing >= 45 && bearing < 135) return "East Corridor";
  if (bearing >= 135 && bearing < 225) return "South Corridor";
  if (bearing >= 225 && bearing < 315) return "West Corridor";
  return "North Corridor";
}



/**
 * Resolves explicitly verified coordinates or an exact master entry.
 * Returns null when a stop needs verification instead of guessing.
 */
export function resolveCoordinates(
  stopName,
  explicitLat,
  explicitLon,
  explicitlyVerified = false
) {
  const lat = Number(explicitLat);
  const lon = Number(explicitLon);
  if (explicitlyVerified &&
      explicitLat !== null && explicitLat !== "" && explicitLon !== null && explicitLon !== "" &&
      Number.isFinite(lat) && Number.isFinite(lon) && isWithinPuneServiceArea(lat, lon)) {
    return [lat, lon];
  }

  const key = normalizeCoordinateKey(stopName);
  const coordinates = NORMALIZED_MASTER_COORDS.get(key);
  return coordinates && isWithinPuneServiceArea(coordinates[0], coordinates[1])
    ? coordinates
    : null;
}

/**
 * Sequences stops along the road traveling towards the final destination drop point
 */
export function sequenceStopsTowardsDestination(
  stops,
  destination = DEFAULT_DESTINATION,
) {
  if (!stops.length) return { orderedStops: [], finalLegKm: 0, totalRouteWithDestinationKm: 0 };
  const estimateMinutes = (distanceKm) =>
    (distanceKm * PUNE_ROAD_FACTOR / FALLBACK_AVERAGE_SPEED_KMH) * 60;
  if (stops.length === 1) {
    const s = stops[0];
    const finalLeg = Number(haversineDistanceKm(s.lat, s.lon, destination.lat, destination.lon).toFixed(1));
    const finalMinutes = Number(estimateMinutes(finalLeg).toFixed(1));
    return {
      orderedStops: [
        {
          seq: 1,
          stop_name: s.name,
          lat: Number(s.lat.toFixed(5)),
          lon: Number(s.lon.toFixed(5)),
          distance_from_prev_km: 0,
          cumulative_km: 0,
          passengers_boarding: s.employees.length,
          cumulative_passengers: s.employees.length,
          employees: s.employees,
          distance_to_destination_km: finalLeg,
          minutes_from_prev: 0,
          cumulative_minutes: 0,
          minutes_to_destination: finalMinutes,
          ride_minutes_to_destination: finalMinutes,
          ride_time_status: getRideTimeStatus(finalMinutes),
        },
      ],
      finalLegKm: finalLeg,
      totalRouteWithDestinationKm: finalLeg,
      totalMinutes: finalMinutes,
    };
  }

  // 1. Initial ordering: start at furthest origin from plant, greedy nearest forward hop
  const unvisited = [...stops];
  let startIdx = 0;
  let maxD = -1;
  for (let i = 0; i < unvisited.length; i++) {
    const d = haversineDistanceKm(unvisited[i].lat, unvisited[i].lon, destination.lat, destination.lon);
    if (d > maxD) {
      maxD = d;
      startIdx = i;
    }
  }

  let route = [unvisited.splice(startIdx, 1)[0]];
  while (unvisited.length > 0) {
    const last = route[route.length - 1];
    let bestIdx = 0;
    let bestScore = Infinity;
    for (let i = 0; i < unvisited.length; i++) {
      const dHop = haversineDistanceKm(last.lat, last.lon, unvisited[i].lat, unvisited[i].lon);
      const dDest = haversineDistanceKm(unvisited[i].lat, unvisited[i].lon, destination.lat, destination.lon);
      const score = dHop + 0.3 * dDest;
      if (score < bestScore) {
        bestScore = score;
        bestIdx = i;
      }
    }
    route.push(unvisited.splice(bestIdx, 1)[0]);
  }

  const passengerRideDistance = (r) => {
    let passengersOnboard = 0;
    let passengerDistance = 0;
    for (let i = 0; i < r.length; i++) {
      passengersOnboard += r[i].employees.length;
      const next = r[i + 1] || destination;
      passengerDistance +=
        passengersOnboard *
        haversineDistanceKm(r[i].lat, r[i].lon, next.lat, next.lon);
    }
    return passengerDistance;
  };

  // Standard 2-opt route untangling: eliminate loops and self-intersecting segments
  let improved = !fast;
  let iterations = 0;
  const routeDist = (r) => {
    let d = 0;
    for (let k = 1; k < r.length; k++) {
      d += haversineDistanceKm(r[k - 1].lat, r[k - 1].lon, r[k].lat, r[k].lon);
    }
    d += haversineDistanceKm(r[r.length - 1].lat, r[r.length - 1].lon, destination.lat, destination.lon);
    return d;
  };

  while (improved && iterations < 50) {
    improved = false;
    iterations++;
    for (let i = 1; i < route.length - 1; i++) {
      for (let j = i + 1; j < route.length; j++) {
        const newRoute = [
          ...route.slice(0, i),
          ...route.slice(i, j + 1).reverse(),
          ...route.slice(j + 1),
        ];
        if (routeDist(newRoute) < routeDist(route) - 0.05) {
          route = newRoute;
          improved = true;
          break;
        }
      }
      if (improved) break;
    }
  }


  // 3. Assemble sequential metrics
  const ordered = [];
  let cumulativeKm = 0.0;
  let cumulativeMinutes = 0.0;
  let cumulativePax = 0;
  let prev = null;

  for (let i = 0; i < route.length; i++) {
    const node = route[i];
    const dist = prev ? haversineDistanceKm(prev.lat, prev.lon, node.lat, node.lon) : 0.0;
    const legMinutes = prev ? estimateMinutes(dist) : 0.0;
    cumulativeKm += dist;
    cumulativeMinutes += legMinutes;
    cumulativePax += node.employees.length;

    ordered.push({
      seq: i + 1,
      stop_name: node.name,
      lat: Number(node.lat.toFixed(5)),
      lon: Number(node.lon.toFixed(5)),
      distance_from_prev_km: Number(dist.toFixed(1)),
      cumulative_km: Number(cumulativeKm.toFixed(1)),
      passengers_boarding: node.employees.length,
      cumulative_passengers: cumulativePax,
      employees: node.employees,
      distance_to_destination_km: Number(
        haversineDistanceKm(node.lat, node.lon, destination.lat, destination.lon).toFixed(1)
      ),
      minutes_from_prev: Number(legMinutes.toFixed(1)),
      cumulative_minutes: Number(cumulativeMinutes.toFixed(1)),
      minutes_to_destination: Number(
        estimateMinutes(haversineDistanceKm(node.lat, node.lon, destination.lat, destination.lon)).toFixed(1)
      ),
    });
    prev = node;
  }

  const lastStop = ordered[ordered.length - 1];
  const finalLegKm = Number(
    haversineDistanceKm(lastStop.lat, lastStop.lon, destination.lat, destination.lon).toFixed(1)
  );
  const finalLegMinutes = estimateMinutes(finalLegKm);
  const totalMinutes = cumulativeMinutes + finalLegMinutes;
  ordered.forEach((stop) => {
    const rideMinutes = Number(
      (totalMinutes - stop.cumulative_minutes).toFixed(1)
    );
    stop.ride_minutes_to_destination = rideMinutes;
    stop.ride_time_status = getRideTimeStatus(rideMinutes);
  });

  return {
    orderedStops: ordered,
    finalLegKm,
    totalRouteWithDestinationKm: Number((cumulativeKm + finalLegKm).toFixed(1)),
    totalMinutes: Number(totalMinutes.toFixed(1)),
  };
}

function getRideTimeStatus(minutes) {
  if (minutes <= TARGET_RIDE_MINUTES) return "ON_TARGET";
  if (minutes <= MAX_RIDE_MINUTES) return "WITHIN_ALLOWANCE";
  return "EXCEEDS_LIMIT";
}

// ==========================================================================
// ROAD-AWARE HELPERS
//
// The sequencing above optimises straight-line (Haversine) distance, but a
// bus burns diesel on ROAD distance. On this network road distance is ~52%
// higher than the straight line - and the gap is NOT uniform. Two stops on
// the same highway are close in both measures; two stops in different
// corridors can look adjacent on a map yet be a long drive apart on the
// road. That is precisely why a Haversine-based optimiser produces a 130 km
// bus and believes it saved kilometres.
//
// When a distance matrix (OSRM /table) is supplied we sequence and compare
// on road distance instead.
// ==========================================================================

const nodeKey = (n) => roadKey(n.lat, n.lon);

/**
 * Sequences stops using real road distances. Candidate grouping uses a fast
 * nearest-neighbour estimate; final routes use Dijkstra for small sets or
 * nearest-neighbour with 2-opt refinement for larger sets.
 */
export function sequenceStopsByRoad(
  stops,
  destination = DEFAULT_DESTINATION,
  matrix,
  fast = false
) {
  if (!matrix || stops.length === 0) {
    return sequenceStopsTowardsDestination(stops, destination, fast);
  }

  const d = (a, b) => matrix.getKm(nodeKey(a), nodeKey(b));
  const t = (a, b) => matrix.getMin(nodeKey(a), nodeKey(b));
  let route;
  if (!fast && stops.length <= EXACT_DIJKSTRA_STOP_LIMIT) {
    route = dijkstraStopOrder(stops, destination, matrix);
  } else {
    // Seed larger problems from the furthest origin, then refine below.
    const unvisited = [...stops];
    let startIdx = 0;
    let maxD = -1;
    for (let i = 0; i < unvisited.length; i++) {
      const dist = d(unvisited[i], destination);
      if (dist > maxD) {
        maxD = dist;
        startIdx = i;
      }
    }

    route = [unvisited.splice(startIdx, 1)[0]];
    while (unvisited.length > 0) {
      const last = route[route.length - 1];
      let bestIdx = 0;
      let bestD = Infinity;
      for (let i = 0; i < unvisited.length; i++) {
        const hop = d(last, unvisited[i]);
        if (hop < bestD) {
          bestD = hop;
          bestIdx = i;
        }
      }
      route.push(unvisited.splice(bestIdx, 1)[0]);
    }
  }

  const passengerRideTime = (r) => {
    let cumulativePassengers = 0;
    let totalPassengerMinutes = 0;
    for (let i = 0; i < r.length; i++) {
      cumulativePassengers += r[i].employees.length;
      const next = i + 1 < r.length ? r[i + 1] : destination;
      totalPassengerMinutes += cumulativePassengers * t(r[i], next);
    }
    return totalPassengerMinutes;
  };

  // Optimize the sum of passenger ride-times rather than distance alone.
  let improved = !fast && stops.length > EXACT_DIJKSTRA_STOP_LIMIT;
  let iterations = 0;
  while (improved && iterations < 60) {
    improved = false;
    iterations++;
    for (let i = 0; i < route.length - 1; i++) {
      for (let j = i + 1; j < route.length; j++) {
        const candidate = [
          ...route.slice(0, i),
          ...route.slice(i, j + 1).reverse(),
          ...route.slice(j + 1),
        ];
        if (passengerRideTime(candidate) < passengerRideTime(route) - 0.01) {
          route.splice(0, route.length, ...candidate);
          improved = true;
          break;
        }
      }
      if (improved) break;
    }
  }

  const ordered = [];
  let cumulativeKm = 0;
  let cumulativeMin = 0;
  let cumulativePax = 0;
  let prev = null;

  for (let i = 0; i < route.length; i++) {
    const node = route[i];
    const leg = prev ? d(prev, node) : 0;
    const legMin = prev ? t(prev, node) : 0;
    cumulativeKm += leg;
    cumulativeMin += legMin;
    cumulativePax += node.employees.length;
    ordered.push({
      seq: i + 1,
      stop_name: node.name,
      lat: Number(node.lat.toFixed(5)),
      lon: Number(node.lon.toFixed(5)),
      distance_from_prev_km: Number(leg.toFixed(1)),
      cumulative_km: Number(cumulativeKm.toFixed(1)),
      minutes_from_prev: Number(legMin.toFixed(1)),
      cumulative_minutes: Number(cumulativeMin.toFixed(1)),
      passengers_boarding: node.employees.length,
      cumulative_passengers: cumulativePax,
      employees: node.employees,
      distance_to_destination_km: Number(d(node, destination).toFixed(1)),
      minutes_to_destination: Number(t(node, destination).toFixed(1)),
    });
    prev = node;
  }

  let totalRoadKm = 0;
  for (let i = 0; i < route.length - 1; i++) {
    totalRoadKm += d(route[i], route[i + 1]);
  }
  totalRoadKm += d(route[route.length - 1], destination);
  totalRoadKm = Number(totalRoadKm.toFixed(1));
  const finalLegKm = Number(d(route[route.length - 1], destination).toFixed(1));
  const totalMinutes = cumulativeMin + t(route[route.length - 1], destination);
  ordered.forEach((stop) => {
    const rideMinutes = Number((totalMinutes - stop.cumulative_minutes).toFixed(1));
    stop.ride_minutes_to_destination = rideMinutes;
    stop.ride_time_status = getRideTimeStatus(rideMinutes);
  });

  return {
    orderedStops: ordered,
    finalLegKm,
    totalRouteWithDestinationKm: totalRoadKm,
    roadKm: totalRoadKm,
    totalMinutes: Number(totalMinutes.toFixed(1)),
  };
}

/**
 * Road km of an already-ordered path (used to price a route we did not
 * re-sequence, e.g. after a split).
 */
export function pathRoadKm(orderedStops, destination = DEFAULT_DESTINATION, matrix) {
  if (!matrix || !orderedStops.length) return null;
  const d = (a, b) => matrix.getKm(nodeKey(a), nodeKey(b));
  let sum = 0;
  for (let i = 0; i < orderedStops.length - 1; i++) {
    sum += d(orderedStops[i], orderedStops[i + 1]);
  }
  sum += d(orderedStops[orderedStops.length - 1], destination);
  return Number(sum.toFixed(1));
}

class MinPriorityQueue {
  constructor() {
    this.items = [];
  }

  push(item) {
    const items = this.items;
    items.push(item);
    let index = items.length - 1;
    while (index > 0) {
      const parent = Math.floor((index - 1) / 2);
      if (items[parent].cost <= item.cost) break;
      items[index] = items[parent];
      index = parent;
    }
    items[index] = item;
  }

  pop() {
    const items = this.items;
    if (!items.length) return null;

    const first = items[0];
    const last = items.pop();
    if (items.length) {
      let index = 0;
      while (true) {
        const left = index * 2 + 1;
        const right = left + 1;
        if (left >= items.length) break;
        const child = right < items.length && items[right].cost < items[left].cost
          ? right
          : left;
        if (items[child].cost >= last.cost) break;
        items[index] = items[child];
        index = child;
      }
      items[index] = last;
    }
    return first;
  }
}

function dijkstraStopOrder(stops, destination, matrix) {
  const n = stops.length;
  const maskCount = 1 << n;
  const fullMask = maskCount - 1;
  const passengerCounts = new Uint16Array(maskCount);
  for (let mask = 1; mask < maskCount; mask++) {
    const bit = mask & -mask;
    const stopIndex = 31 - Math.clz32(bit);
    passengerCounts[mask] =
      passengerCounts[mask ^ bit] + stops[stopIndex].employees.length;
  }
  const stateCount = maskCount * n;
  const distances = new Float64Array(stateCount);
  distances.fill(Infinity);
  const previous = new Int32Array(stateCount);
  previous.fill(-1);
  const queue = new MinPriorityQueue();

  for (let i = 0; i < n; i++) {
    const state = (1 << i) * n + i;
    distances[state] = 0;
    queue.push({ state, cost: 0 });
  }

  const destinationKey = roadKey(destination.lat, destination.lon);
  let bestState = -1;
  let bestCost = Infinity;

  while (queue.items.length) {
    const current = queue.pop();
    if (current.cost !== distances[current.state]) continue;

    const mask = Math.floor(current.state / n);
    const stopIndex = current.state % n;
    if (mask === fullMask) {
      const finalLegMinutes = matrix.getMin(
        nodeKey(stops[stopIndex]),
        destinationKey
      );
      const routeCost =
        current.cost + finalLegMinutes * passengerCounts[mask];
      if (routeCost < bestCost) {
        bestCost = routeCost;
        bestState = current.state;
      }
      continue;
    }

    for (let next = 0; next < n; next++) {
      const nextBit = 1 << next;
      if (mask & nextBit) continue;

      const legMinutes = matrix.getMin(
        nodeKey(stops[stopIndex]),
        nodeKey(stops[next])
      );
      if (!Number.isFinite(legMinutes)) continue;

      const nextMask = mask | nextBit;
      const nextState = nextMask * n + next;
      const nextCost = current.cost + legMinutes * passengerCounts[mask];
      if (nextCost < distances[nextState]) {
        distances[nextState] = nextCost;
        previous[nextState] = current.state;
        queue.push({ state: nextState, cost: nextCost });
      }
    }
  }

  if (bestState < 0 || !Number.isFinite(bestCost)) {
    throw new Error("No road route visits every pickup stop and reaches the destination.");
  }

  const orderedIndices = [];
  for (let state = bestState; state >= 0; state = previous[state]) {
    orderedIndices.push(state % n);
  }
  orderedIndices.reverse();
  return orderedIndices.map((index) => stops[index]);
}

/**
 * Groups employees based on their PICKUP POINTS, chaining them into a bus route leading to the drop point
 */
export function clusterStopsIntoRoutes(employees, options = {}) {
  const busCapacity = BUS_CAPACITY;
  const groupByShift = options.groupByShift !== false;
  const destination = options.destination || DEFAULT_DESTINATION;
  const disableMerge = options.disableMerge === true;
  const disableSplit = options.disableSplit === true;
  // The matrix provides estimated no-traffic road durations and distances.
  const roadMatrix = options.roadMatrix || null;

  const routeableEmployees = [];
  const unverifiedEmployees = [];
  employees.forEach((employee) => {
    const coordinates =
      employee.location_status === "LOCATION_UNVERIFIED"
        ? null
        : employee.location_status === "VERIFIED"
        ? resolveCoordinates(
            employee.pickup_point,
            employee.latitude,
            employee.longitude,
            true
          )
        : resolveCoordinates(employee.pickup_point);
    if (!coordinates) {
      unverifiedEmployees.push(employee);
      return;
    }
    routeableEmployees.push({
      ...employee,
      latitude: coordinates[0],
      longitude: coordinates[1],
      location_status: "VERIFIED",
    });
  });

  // Separate employees by shift pools
  // Note: Combined shifts (A+B and A+B+C) are grouped into Shift A as requested
  const shiftPools = {};
  routeableEmployees.forEach((emp) => {
    let rawShift = String(emp.shift || "A").trim().toUpperCase().replace(/\s+/g, "");
    if (rawShift.includes("A+B") || rawShift.includes("A+B+C")) {
      rawShift = "A";
    }
    const shift = groupByShift ? rawShift : "ALL";
    if (!shiftPools[shift]) shiftPools[shift] = [];
    shiftPools[shift].push(emp);
  });

  const routes = [];
  const assignedEmployees = [];

  Object.entries(shiftPools).forEach(([shift, empList]) => {
    // 1. Group employees strictly by their exact PICKUP POINT (bus stop)
    const stopMap = {};
    empList.forEach((e) => {
      const stop = e.pickup_point;
      if (!stopMap[stop]) {
        const coordinates = resolveCoordinates(
          stop,
          e.latitude,
          e.longitude,
          e.location_status === "VERIFIED"
        );
        if (!coordinates) {
          throw new Error(`No verified coordinates found for pickup point "${stop}".`);
        }
        const [lat, lon] = coordinates;
        stopMap[stop] = {
          name: stop,
          lat,
          lon,
          employees: [],
          distToDest: haversineDistanceKm(lat, lon, destination.lat, destination.lon),
        };
      }
      stopMap[stop].employees.push(e);
    });

    // Partition stops into natural geographic highway corridors leading to the plant
    const corridorPools = {};
    Object.values(stopMap).forEach((stop) => {
      const cid = getCorridorId(stop.lat, stop.lon, destination);
      if (!corridorPools[cid]) corridorPools[cid] = [];
      corridorPools[cid].push(stop);
    });

    let busNum = 1;

    // Helper to register an ordered route
    const registerRoute = (clusterStops, corridorName) => {
      // Sequence on real road distance when a matrix is available, so the
      // route the bus actually drives is the one we measure and optimise.
      const { orderedStops, finalLegKm, totalRouteWithDestinationKm, totalMinutes } =
        sequenceStopsByRoad(clusterStops, destination, roadMatrix);

      const busId = `Bus ${shift}-${busNum}`;
      busNum++;

      const totalPassengers = orderedStops[orderedStops.length - 1].cumulative_passengers;
      const maxJump = Math.max(...orderedStops.map((s) => s.distance_from_prev_km));
      const longestRideMinutes = Math.max(
        ...orderedStops.map((stop) => stop.ride_minutes_to_destination || 0)
      );
      const rideTimeStatus =
        longestRideMinutes <= TARGET_RIDE_MINUTES
          ? "ON_TARGET"
          : longestRideMinutes <= MAX_RIDE_MINUTES
            ? "WITHIN_ALLOWANCE"
            : "EXCEEDS_LIMIT";
      const passengerRideMinutes = orderedStops.reduce(
        (total, stop) =>
            total +
            stop.ride_minutes_to_destination * stop.passengers_boarding,
        0
      );

      routes.push({
        bus_id: busId,
        shift: shift === "ALL" ? "General" : `Shift ${shift}`,
        corridor: corridorName || "Talegaon Local",
        passengers_assigned: totalPassengers,
        capacity: busCapacity,
        seats_available: Math.max(0, busCapacity - totalPassengers),
        total_stops: orderedStops.length,
        total_km: totalRouteWithDestinationKm,
        road_km: roadMatrix ? totalRouteWithDestinationKm : null,
        total_minutes: totalMinutes ?? 0,
        longest_ride_minutes: Number(longestRideMinutes.toFixed(1)),
        ride_time_status: rideTimeStatus,
        passenger_ride_minutes: Number(passengerRideMinutes.toFixed(1)),
        stops_only_km: orderedStops[orderedStops.length - 1].cumulative_km,
        final_leg_to_destination_km: finalLegKm,
        destination_name: destination.name,
        max_jump_km: Number(maxJump.toFixed(1)),
        start_stop: orderedStops[0].stop_name,
        end_stop: destination.name,
        stops: orderedStops,
        destination,
      });

      // Map each individual employee to this bus with their stop coordinates
      orderedStops.forEach((st) => {
        st.employees.forEach((emp) => {
          assignedEmployees.push({
            sr_no: assignedEmployees.length + 1,
            employee_id: emp.employee_id || "-",
            employee_name: emp.employee_name,
            grade: emp.grade || "",
            shift: emp.shift,
            corridor: corridorName || "Talegaon Local",
            pickup_point: emp.pickup_point,
            location_context: emp.location_context || "",
            assigned_bus: busId,
            mobile: emp.mobile || "-",
            lat: st.lat,
            lon: st.lon,
            seq: st.seq,
          });
        });
      });
    };

    Object.entries(corridorPools).forEach(([corridorName, corridorStops]) => {
      const stopNodes = corridorStops.flatMap((stop) => {
        const batches = [];
        for (let i = 0; i < stop.employees.length; i += busCapacity) {
          batches.push({
            ...stop,
            employees: stop.employees.slice(i, i + busCapacity),
          });
        }
        return batches;
      });
      const unvisited = new Set(stopNodes);
      const proximityMinutes = (a, b) =>
        roadMatrix
          ? roadMatrix.getMin(nodeKey(a), nodeKey(b))
          : (haversineDistanceKm(a.lat, a.lon, b.lat, b.lon) /
              FALLBACK_AVERAGE_SPEED_KMH) *
            60;

      while (unvisited.size) {
        const remaining = [...unvisited];
        remaining.sort((a, b) => {
          const aTime = roadMatrix
            ? roadMatrix.getMin(nodeKey(a), roadKey(destination.lat, destination.lon))
            : a.distToDest / FALLBACK_AVERAGE_SPEED_KMH * 60;
          const bTime = roadMatrix
            ? roadMatrix.getMin(nodeKey(b), roadKey(destination.lat, destination.lon))
            : b.distToDest / FALLBACK_AVERAGE_SPEED_KMH * 60;
          return bTime - aTime;
        });
        const clusterStops = [remaining[0]];
        unvisited.delete(remaining[0]);

        // Pseudo-node for destination (used in detour calculations)
        const destNode = { lat: destination.lat, lon: destination.lon };

        while (unvisited.size) {
          const currentPassengers = clusterStops.reduce(
            (count, stop) => count + stop.employees.length,
            0
          );

          const lastStop = clusterStops[clusterStops.length - 1];
          const directToDestMinutes = proximityMinutes(lastStop, destNode);
          const candidates = [];

          for (const candidate of unvisited) {
            // Strictly enforce bus capacity limit
            if (currentPassengers + candidate.employees.length > busCapacity) continue;

            // Hop distance between consecutive stops along highway corridors (<= 10 km)
            const hopDistKm = haversineDistanceKm(lastStop.lat, lastStop.lon, candidate.lat, candidate.lon);
            if (hopDistKm > 10.0) continue;

            const viaMinutes =
              proximityMinutes(lastStop, candidate) +
              proximityMinutes(candidate, destNode);
            const detourMinutes = viaMinutes - directToDestMinutes;

            // Reject any stop that adds more than 12 min of detour
            if (detourMinutes > 12) continue;

            // STRICT HARD CEILING: Total commute time must NEVER exceed TARGET_RIDE_MINUTES (60 min) or MAX_RIDE_MINUTES (70 min)
            const testStops = [...clusterStops, candidate];
            let estKm = 0;
            for (let ti = 1; ti < testStops.length; ti++) {
              estKm += haversineDistanceKm(testStops[ti - 1].lat, testStops[ti - 1].lon, testStops[ti].lat, testStops[ti].lon);
            }
            estKm += haversineDistanceKm(candidate.lat, candidate.lon, destNode.lat, destNode.lon);
            const estRoadKm = estKm * PUNE_ROAD_FACTOR;
            const estMin = (estRoadKm / FALLBACK_AVERAGE_SPEED_KMH) * 60 + testStops.length * DWELL_MINUTES_PER_STOP;
            if (estMin > TARGET_RIDE_MINUTES) continue;

            candidates.push({ candidate, detourMinutes, hopDistKm });
          }

          if (!candidates.length) break;

          candidates.sort((a, b) => a.detourMinutes - b.detourMinutes);
          clusterStops.push(candidates[0].candidate);
          unvisited.delete(candidates[0].candidate);
        }

        registerRoute(clusterStops, corridorName);
      }
    });
  });

  // ==========================================================================
  // FLEET MERGE PASS — collapse overlapping buses into fewer, fuller buses.
  //
  // Two buses covering the same physical road (e.g. both running the Talegaon
  // -> plant corridor) burn diesel twice over the identical stretch. If one
  // bus can cover BOTH sets of stops within seat capacity, the fleet shrinks
  // and total fleet-km drops.
  //
  // A merge is accepted only when ALL of these hold:
  //   1. Same shift (a bus cannot serve two shift timings).
  //   2. Combined passengers <= physical bus capacity.
  //   3. Merged route's longest hop <= maxJumpKm (no silly backtracking).
  //   4. Journey-time limits are met when road metrics are available.
  //   5. For the bus-first objective, prefer the fewest feasible buses, then
  //      choose merges with the smallest distance penalty.
  // Cross-corridor merges are allowed, but only when shortest-road-path
  // sequencing reduces total fleet kilometres and satisfies the journey
  // time and capacity constraints.
  // ==========================================================================
  const toRawStop = (s) => ({
    name: s.stop_name ?? s.name,
    lat: s.lat,
    lon: s.lon,
    employees: s.employees || [],
  });

  const mergeRoutes = (routeList) => {
    let working = [...routeList];

    // Best-improvement search: evaluate every legal pair, apply the single
    // best (largest km saving), repeat until nothing more can be merged.
    for (;;) {
      let best = null;

      for (let i = 0; i < working.length; i++) {
        for (let j = i + 1; j < working.length; j++) {
          const a = working[i];
          const b = working[j];

          if (a.shift !== b.shift) continue;
          // Allow merge within same corridor, or absorb adjacent low-occupancy outliers (<= 5 pax within 8 km)
          if (a.corridor !== b.corridor) {
            const hasLowOccupancy = a.passengers_assigned <= 5 || b.passengers_assigned <= 5;
            if (!hasLowOccupancy) continue;

            let minDistanceKm = Infinity;
            for (const sA of a.stops) {
              for (const sB of b.stops) {
                const dist = haversineDistanceKm(sA.lat, sA.lon, sB.lat, sB.lon);
                if (dist < minDistanceKm) minDistanceKm = dist;
              }
            }
            if (minDistanceKm > 8.0) continue;
          }


          const combinedPax = a.passengers_assigned + b.passengers_assigned;
          if (combinedPax > busCapacity) continue;

          const combinedStops = [...a.stops, ...b.stops].map(toRawStop);
          const { orderedStops, totalRouteWithDestinationKm, totalMinutes } =
            sequenceStopsByRoad(combinedStops, destination, roadMatrix);
          const longestRideMinutes = Math.max(
            ...orderedStops.map((stop) => stop.ride_minutes_to_destination || 0)
          );
          if (longestRideMinutes > TARGET_RIDE_MINUTES || totalRouteWithDestinationKm > 42.0) continue;

          const passengerRideMinutes = orderedStops.reduce(
            (total, stop) =>
              total +
              stop.ride_minutes_to_destination * stop.passengers_boarding,
            0
          );
          const rideTimeIncrease =
            passengerRideMinutes -
            (a.passenger_ride_minutes + b.passenger_ride_minutes);
          const targetPriority =
            longestRideMinutes <= TARGET_RIDE_MINUTES ? 0 : 1;

          if (
            !best ||
            targetPriority < best.targetPriority ||
            (targetPriority === best.targetPriority &&
              rideTimeIncrease < best.rideTimeIncrease)
          ) {
            best = {
              a,
              b,
              orderedStops,
              fleetKmAfter: totalRouteWithDestinationKm,
              totalMinutes: totalMinutes ?? 0,
              combinedPax,
              longestRideMinutes,
              passengerRideMinutes,
              targetPriority,
              rideTimeIncrease,
            };
          }
        }
      }

      if (!best) break; // no more legal merges

      const mergedRoute = {
        bus_id: best.a.bus_id,
        shift: best.a.shift,
        corridor: best.a.corridor,
        passengers_assigned: best.combinedPax,
        capacity: busCapacity,
        seats_available: Math.max(0, busCapacity - best.combinedPax),
        total_stops: best.orderedStops.length,
        total_km: best.fleetKmAfter,
        road_km: roadMatrix ? best.fleetKmAfter : null,
        total_minutes: best.totalMinutes,
        longest_ride_minutes: Number(best.longestRideMinutes.toFixed(1)),
        ride_time_status:
          best.longestRideMinutes <= TARGET_RIDE_MINUTES
            ? "ON_TARGET"
            : "WITHIN_ALLOWANCE",
        passenger_ride_minutes: Number(best.passengerRideMinutes.toFixed(1)),
        stops_only_km: best.orderedStops[best.orderedStops.length - 1].cumulative_km,
        final_leg_to_destination_km:
          best.orderedStops[best.orderedStops.length - 1].distance_to_destination_km,
        destination_name: destination.name,
        max_jump_km: Number(
          Math.max(
            ...best.orderedStops.map((stop) => stop.distance_from_prev_km)
          ).toFixed(1)
        ),
        start_stop: best.orderedStops[0].stop_name,
        end_stop: destination.name,
        stops: best.orderedStops,
        destination,
        merged_from: [best.a.bus_id, best.b.bus_id],
      };

      working = working.filter((r) => r !== best.a && r !== best.b);
      working.push(mergedRoute);
    }

    return working;
  };

  // Split any merged route that cannot satisfy the maximum passenger ride time.
  const splitOversizedRoutes = (routeList) => {
    let working = [...routeList];
    let changed = true;

    while (changed) {
      changed = false;
      const idx = working.findIndex((r) =>
        (r.longest_ride_minutes > TARGET_RIDE_MINUTES || r.total_minutes > TARGET_RIDE_MINUTES || r.total_km > 42.0) && r.stops.length >= 2
      );
      if (idx === -1) break;

      const route = working[idx];
      const ordered = route.stops;

      // Try every cut point along the ordered route, keep the one that minimizes maximum commute time.
      let bestCut = null;
      let minMaxRide = Infinity;
      for (let cut = 1; cut < ordered.length; cut++) {
        const head = ordered.slice(0, cut);
        const tail = ordered.slice(cut);

        const headPax = head.reduce((n, s) => n + (s.employees?.length || 0), 0);
        const tailPax = tail.reduce((n, s) => n + (s.employees?.length || 0), 0);
        if (headPax > busCapacity || tailPax > busCapacity) continue;

        const headSeq = sequenceStopsByRoad(head.map(toRawStop), destination, roadMatrix);
        const tailSeq = sequenceStopsByRoad(tail.map(toRawStop), destination, roadMatrix);
        const maxRide = Math.max(
          ...headSeq.orderedStops.map((stop) => stop.ride_minutes_to_destination || 0),
          ...tailSeq.orderedStops.map((stop) => stop.ride_minutes_to_destination || 0)
        );

        const passengerMinutes =
          headSeq.orderedStops.reduce(
            (total, stop) =>
              total + stop.ride_minutes_to_destination * stop.passengers_boarding,
            0
          ) +
          tailSeq.orderedStops.reduce(
            (total, stop) =>
              total + stop.ride_minutes_to_destination * stop.passengers_boarding,
            0
          );
        if (!bestCut || maxRide < minMaxRide || (maxRide === minMaxRide && passengerMinutes < bestCut.passengerMinutes)) {
          minMaxRide = maxRide;
          bestCut = {
            cut,
            headSeq,
            tailSeq,
            headPax,
            tailPax,
            passengerMinutes,
            maxRide,
          };
        }
      }

      if (!bestCut) {
        const cut = Math.max(1, Math.floor(ordered.length / 2));
        const head = ordered.slice(0, cut);
        const tail = ordered.slice(cut);
        bestCut = {
          cut,
          headSeq: sequenceStopsByRoad(head.map(toRawStop), destination, roadMatrix),
          tailSeq: sequenceStopsByRoad(tail.map(toRawStop), destination, roadMatrix),
          headPax: head.reduce((n, s) => n + (s.employees?.length || 0), 0),
          tailPax: tail.reduce((n, s) => n + (s.employees?.length || 0), 0),
        };
      }

      const makeRoute = (seq, pax, suffix) => {
        const longestRideMinutes = Math.max(
          ...seq.orderedStops.map((stop) => stop.ride_minutes_to_destination || 0)
        );
        const passengerRideMinutes = seq.orderedStops.reduce(
          (total, stop) =>
            total +
            stop.ride_minutes_to_destination * stop.passengers_boarding,
          0
        );
        return {
        bus_id: `${route.bus_id}${suffix}`,
        shift: route.shift,
        corridor: route.corridor,
        passengers_assigned: pax,
        capacity: busCapacity,
        seats_available: Math.max(0, busCapacity - pax),
        total_stops: seq.orderedStops.length,
        total_km: seq.totalRouteWithDestinationKm,
        road_km: roadMatrix ? seq.totalRouteWithDestinationKm : null,
        total_minutes: seq.totalMinutes ?? 0,
        longest_ride_minutes: Number(longestRideMinutes.toFixed(1)),
        ride_time_status:
          longestRideMinutes <= TARGET_RIDE_MINUTES
            ? "ON_TARGET"
            : "WITHIN_ALLOWANCE",
        passenger_ride_minutes: Number(passengerRideMinutes.toFixed(1)),
        stops_only_km: seq.orderedStops[seq.orderedStops.length - 1].cumulative_km,
        final_leg_to_destination_km:
          seq.orderedStops[seq.orderedStops.length - 1].distance_to_destination_km,
        destination_name: destination.name,
        max_jump_km: Number(
          Math.max(...seq.orderedStops.map((s) => s.distance_from_prev_km)).toFixed(1)
        ),
        start_stop: seq.orderedStops[0].stop_name,
        end_stop: destination.name,
        stops: seq.orderedStops,
        destination,
        split_from: route.bus_id,
        };
      };

      working.splice(
        idx,
        1,
        makeRoute(bestCut.headSeq, bestCut.headPax, "a"),
        makeRoute(bestCut.tailSeq, bestCut.tailPax, "b")
      );
      changed = true;
    }

    return working;
  };

  let finalRoutes = disableMerge ? routes : mergeRoutes(routes);
  if (!disableSplit) {
    finalRoutes = splitOversizedRoutes(finalRoutes);
  }
  // Re-assign bus ids so they read A-1, A-2... within each shift,
  // and keep the routes grouped by shift for a tidy reading order.
  const shiftOrder = ["A", "B", "C", "ALL"];
  finalRoutes.sort((a, b) => {
    const ka = a.shift === "General" ? "ALL" : a.shift.replace("Shift ", "");
    const kb = b.shift === "General" ? "ALL" : b.shift.replace("Shift ", "");
    const sa = shiftOrder.indexOf(ka);
    const sb = shiftOrder.indexOf(kb);
    if (sa !== sb) return sa - sb;
    return (
      a.longest_ride_minutes - b.longest_ride_minutes ||
      a.passengers_assigned - b.passengers_assigned
    );
  });

  const counters = {};
  finalRoutes.forEach((r) => {
    const shiftKey = r.shift === "General" ? "ALL" : r.shift.replace("Shift ", "");
    counters[shiftKey] = (counters[shiftKey] || 0) + 1;
    r.bus_id = `Bus ${shiftKey}-${counters[shiftKey]}`;
  });

  // Rebuild the employee assignments from the final (possibly merged) routes
  const finalEmployees = [];
  finalRoutes.forEach((route) => {
    route.stops.forEach((st) => {
      (st.employees || []).forEach((emp) => {
        finalEmployees.push({
          sr_no: finalEmployees.length + 1,
          employee_id: emp.employee_id || "-",
          employee_name: emp.employee_name,
          grade: emp.grade || "",
          shift: emp.shift,
          corridor: route.corridor,
          pickup_point: emp.pickup_point,
          location_context: emp.location_context || "",
          assigned_bus: route.bus_id,
          mobile: emp.mobile || "-",
          lat: st.lat,
          lon: st.lon,
          seq: st.seq,
          ride_minutes_to_destination: st.ride_minutes_to_destination,
          ride_time_status: st.ride_time_status,
          location_status: "VERIFIED",
        });
      });
    });
  });

  const unassignedEmployees = unverifiedEmployees.map((employee, index) => ({
    sr_no: finalEmployees.length + index + 1,
    employee_id: employee.employee_id || "-",
    employee_name: employee.employee_name || "",
    grade: employee.grade || "",
    shift: employee.shift || "",
    corridor: "",
    pickup_point: employee.pickup_point || "",
    location_context: employee.location_context || "",
    assigned_bus: "Unassigned",
    mobile: employee.mobile || "-",
    lat: null,
    lon: null,
    ride_minutes_to_destination: null,
    ride_time_status: "LOCATION_UNVERIFIED",
    location_status: "LOCATION_UNVERIFIED",
    resolution_reason:
      employee.resolution_reason || "No verified pickup coordinates available.",
  }));
  const rosterEmployees = [...finalEmployees, ...unassignedEmployees];

  return {
    routes: finalRoutes,
    employees: rosterEmployees,
    total_employees: rosterEmployees.length,
    routed_employees: finalEmployees.length,
    unverified_employees: unassignedEmployees.length,
    total_buses: finalRoutes.length,
    total_stops: finalRoutes.reduce((sum, r) => sum + r.total_stops, 0),
    total_fleet_km: Number(
      finalRoutes.reduce((sum, r) => sum + r.total_km, 0).toFixed(1)
    ),
    destination,
  };
}
