/**
 * Road Network Routing Utility via OSRM (Open Source Routing Machine)
 * Fetches real driving roads, actual turn-by-turn geometry, real road distance, and travel time.
 */

const routeCache = new Map();

export async function fetchRoadRoute(waypoints) {
  if (!waypoints || waypoints.length < 2) return null;

  const cacheKey = waypoints.map(([lat, lon]) => `${lat.toFixed(5)},${lon.toFixed(5)}`).join(";");
  if (routeCache.has(cacheKey)) {
    return routeCache.get(cacheKey);
  }

  // OSRM coordinates format: lon,lat;lon,lat;...
  const coordQuery = waypoints.map(([lat, lon]) => `${lon},${lat}`).join(";");
  const url = `https://router.project-osrm.org/route/v1/driving/${coordQuery}?overview=full&geometries=geojson`;

  const response = await fetch(url);
  const data = await response.json();

  if (data.code === "Ok" && data.routes && data.routes.length > 0) {
    const route = data.routes[0];
    // Convert GeoJSON [lon, lat] coordinates to Leaflet [lat, lon]
    const roadLatLngs = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
    const roadDistanceKm = Number((route.distance / 1000).toFixed(1));
    const durationMinutes = Math.round(route.duration / 60);

    const result = {
      coordinates: roadLatLngs,
      distanceKm: roadDistanceKm,
      durationMinutes,
      legs: route.legs,
    };

    routeCache.set(cacheKey, result);
    return result;
  }

  throw new Error(data.message || "Failed to retrieve real road route.");
}
