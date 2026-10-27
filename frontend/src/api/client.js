/**
 * AutoRoster API Client.
 *
 * Dedicated HTTP client providing clean methods for all transit planning endpoints.
 */

export async function checkHealth() {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(`Health check failed: HTTP ${res.status}`);
  return await res.json();
}

export async function loadCurrentRoster() {
  const res = await fetch("/api/load_current");
  if (!res.ok) throw new Error(`Failed to load current roster: HTTP ${res.status}`);
  return await res.json();
}

export async function optimizeRoutes(employees, options = {}) {
  const res = await fetch("/api/optimize_all", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      employees,
      shift_filter: options.shiftFilter,
      bus_capacity: options.busCapacity || 32,
      destination: options.destination,
    }),
  });
  if (!res.ok) throw new Error(`Optimization failed: HTTP ${res.status}`);
  return await res.json();
}

export async function resolveSingleStop(stopName, lat = null, lon = null) {
  const res = await fetch("/api/resolve_single", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      stop_name: stopName,
      latitude: lat,
      longitude: lon,
    }),
  });
  if (!res.ok) throw new Error(`Failed to resolve stop: HTTP ${res.status}`);
  return await res.json();
}

export async function exportExcelWorkbook(routes, employees, unverifiedEmployees = []) {
  const res = await fetch("/api/export_excel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      routes,
      employees,
      unverified_employees: unverifiedEmployees,
    }),
  });
  if (!res.ok) throw new Error(`Excel export failed: HTTP ${res.status}`);
  const blob = await res.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "AUTOROSTER_DISPATCH_MANIFEST.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
