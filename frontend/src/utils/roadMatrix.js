/**
 * Road distance matrix via the OSRM /table service.
 *
 * The optimizer needs ROAD distance, not straight-line distance. On this
 * network road distance is ~52% higher than the straight line, and - crucially
 * - the gap is uneven: it is small between two stops on the same highway and
 * large between stops in different corridors. Optimising on Haversine
 * therefore produces routes that look short on a map but are long on tarmac.
 *
 * The public OSRM demo server accepts at most 100 coordinates per table
 * request, so larger problems are fetched in chunks and stitched together.
 * Self-hosting OSRM removes this limit entirely.
 */

const OSRM_TABLE = "https://router.project-osrm.org/table/v1/driving";
const MAX_COORDS_PER_REQUEST = 100;

const matrixCache = new Map();

/**
 * Canonical key for a node.
 *
 * MUST be identical wherever a node is referenced: the matrix build, the
 * matrix lookup, and the route sequencer. Coordinates get rounded to 5
 * decimal places when a route is assembled, so the key rounds too - otherwise
 * a lookup silently misses and returns 0 km, which quietly corrupts every
 * downstream comparison.
 */
export const roadKey = (lat, lon) =>
  `${Number(lat).toFixed(5)},${Number(lon).toFixed(5)}`;

/**
 * A distance matrix over a set of nodes.
 */
export class RoadMatrix {
  constructor(keys, distances, durations) {
    this.keys = keys;
    this.index = new Map(keys.map((k, i) => [k, i]));
    this.distances = distances; // metres, distances[i][j]
    this.durations = durations; // seconds, durations[i][j]
  }

  /** Distance in km between two nodes, keyed by "lat,lon". */
  getKm(keyA, keyB) {
    const i = this.index.get(keyA);
    const j = this.index.get(keyB);
    if (i === undefined || j === undefined) return Infinity;
    const metres = this.distances[i][j];
    if (metres === null || metres === undefined) return Infinity;
    return metres / 1000;
  }

  /** Duration in minutes between two nodes, keyed by "lat,lon". */
  getMin(keyA, keyB) {
    if (!this.durations) return Infinity;
    const i = this.index.get(keyA);
    const j = this.index.get(keyB);
    if (i === undefined || j === undefined) return Infinity;
    const seconds = this.durations[i][j];
    if (seconds === null || seconds === undefined) return Infinity;
    return seconds / 60;
  }
}

/**
 * Builds a road matrix for the given nodes.
 *
 * @param {Array<{lat:number,lon:number}>} nodes
 * @returns {Promise<RoadMatrix>}
 */
export async function buildRoadMatrix(nodes) {
  const keys = nodes.map((n) => roadKey(n.lat, n.lon));
  const cacheKey = keys.join(";");
  if (matrixCache.has(cacheKey)) return matrixCache.get(cacheKey);

  const n = keys.length;
  const distances = Array.from({ length: n }, () => new Array(n).fill(null));
  const durations = Array.from({ length: n }, () => new Array(n).fill(null));

  if (n < 2) {
    if (n === 1) {
      distances[0][0] = 0;
      durations[0][0] = 0;
    }
  } else if (n <= MAX_COORDS_PER_REQUEST) {
    const chunk = await fetchChunk(keys);
    for (let i = 0; i < n; i++) {
      for (let j = 0; j < n; j++) {
        distances[i][j] = chunk.distances[i][j];
        durations[i][j] = chunk.durations[i][j];
      }
    }
  } else {
    // Block fetch: split the rows into chunks, and for each row-chunk fetch
    // the columns in chunks too, so no single request exceeds the cap.
    const blockSize = MAX_COORDS_PER_REQUEST;
    for (let bi = 0; bi < n; bi += blockSize) {
      const rowKeys = keys.slice(bi, bi + blockSize);
      for (let bj = 0; bj < n; bj += blockSize) {
        const colKeys = keys.slice(bj, bj + blockSize);
        const combined = [...rowKeys, ...colKeys];
        const chunk = await fetchChunk(combined);
        const rows = rowKeys.length;
        for (let i = 0; i < rows; i++) {
          for (let j = 0; j < colKeys.length; j++) {
            distances[bi + i][bj + j] = chunk.distances[i][rows + j];
            durations[bi + i][bj + j] = chunk.durations[i][rows + j];
          }
        }
      }
    }
  }

  const matrix = new RoadMatrix(keys, distances, durations);
  matrixCache.set(cacheKey, matrix);
  return matrix;
}

async function fetchChunk(keys) {
  // OSRM expects lon,lat pairs. Our internal keys are "lat,lon", so swap.
  const coords = keys
    .map((k) => {
      const [lat, lon] = k.split(",");
      return `${lon},${lat}`;
    })
    .join(";");
  const url = `${OSRM_TABLE}/${coords}?annotations=distance,duration`;

  // The public demo server resets connections under load. Retry with
  // backoff rather than losing the whole optimisation.
  let lastErr = null;
  for (let attempt = 0; attempt < 4; attempt++) {
    try {
      const res = await fetch(url);
      const data = await res.json();
      if (data.code !== "Ok") {
        // A "TooBig" / "NoRoute" response is deterministic - do not retry.
        throw new Error(`OSRM table failed: ${data.code} ${data.message || ""}`);
      }
      return { distances: data.distances, durations: data.durations };
    } catch (err) {
      lastErr = err;
      // Deterministic failures (bad request) will never succeed on retry.
      if (String(err.message).includes("OSRM table failed")) throw err;
      await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
    }
  }
  throw lastErr;
}

/**
 * Builds a matrix for the destination plus every unique stop in the routes.
 */
export async function buildRouteMatrix(routes, destination) {
  const nodeMap = new Map();
  const destKey = roadKey(destination.lat, destination.lon);
  nodeMap.set(destKey, { lat: destination.lat, lon: destination.lon, name: destination.name });

  routes.forEach((r) => {
    (r.stops || []).forEach((s) => {
      const key = roadKey(s.lat, s.lon);
      if (!nodeMap.has(key)) {
        nodeMap.set(key, { lat: s.lat, lon: s.lon, name: s.stop_name || s.name || "" });
      }
    });
  });

  return buildRoadMatrix([...nodeMap.values()]);
}
