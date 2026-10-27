import React, { useEffect, useRef, useState, useMemo } from "react";
import L from "leaflet";
import { fetchRoadRoute } from "../utils/routing";
import { X } from "lucide-react";

const ROUTE_COLORS = [
  "#5B6EE1", // indigo
  "#3FA796", // teal
  "#E39A4B", // amber
  "#9A7BDB", // violet
  "#E5607A", // rose
  "#4E9BD0", // sky
  "#7FB069", // sage
  "#D9B44A", // gold
  "#C46FB3", // orchid
  "#6C7A99", // slate
  "#3FBFD0", // cyan
  "#E07B5F", // coral
];

export default function RouteMap({
  routes = [],
  destination,
  activeBusId,
  hoveredEmployee,
  onSelectBus,
}) {
  const mapRef = useRef(null);
  const mapInstanceRef = useRef(null);
  const layersRef = useRef([]);

  // Filter & Selection State
  const [selectedShift, setSelectedShift] = useState("ALL");
  const [selectedCorridor, setSelectedCorridor] = useState("ALL");
  const [selectedBusIds, setSelectedBusIds] = useState(
    activeBusId && activeBusId !== "ALL" ? [activeBusId] : []
  );
  const [selectedStopData, setSelectedStopData] = useState(null);
  const [roadStatsMap, setRoadStatsMap] = useState({});
  const [isLoadingRoads, setIsLoadingRoads] = useState(false);

  // Sync with prop if changed from outside
  useEffect(() => {
    if (activeBusId && activeBusId !== "ALL") {
      setSelectedBusIds([activeBusId]);
    } else if (activeBusId === "ALL" || !activeBusId) {
      setSelectedBusIds([]);
    }
  }, [activeBusId]);

  // Handle window resizing and tab switching so Leaflet redraws tiles properly
  useEffect(() => {
    const handleResize = () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.invalidateSize();
      }
    };
    window.addEventListener("resize", handleResize);
    const timer = setTimeout(handleResize, 300);
    return () => {
      window.removeEventListener("resize", handleResize);
      clearTimeout(timer);
    };
  }, []);

  // Available unique corridors
  const corridors = useMemo(() => {
    const set = new Set();
    routes.forEach((r) => {
      if (r.corridor) set.add(r.corridor);
    });
    return ["ALL", ...Array.from(set)];
  }, [routes]);

  // Filter routes by shift and corridor
  const displayedRoutes = useMemo(() => {
    if (!routes || !routes.length) return [];
    return routes.filter((r) => {
      const matchShift =
        selectedShift === "ALL" ||
        (r.shift && r.shift.toUpperCase().includes(selectedShift));
      const matchCorridor =
        selectedCorridor === "ALL" || r.corridor === selectedCorridor;
      return matchShift && matchCorridor;
    });
  }, [routes, selectedShift, selectedCorridor]);

  // Color mapping helper
  const getBusColor = (busId) => {
    const idx = routes.findIndex((r) => r.bus_id === busId);
    return ROUTE_COLORS[(idx >= 0 ? idx : 0) % ROUTE_COLORS.length];
  };

  // Fetch real road metrics for all displayed routes in parallel
  useEffect(() => {
    if (!displayedRoutes.length || !destination || !destination.lat) return;

    let isMounted = true;
    setIsLoadingRoads(true);

    const fetchAll = async () => {
      const statsObj = {};
      const promises = displayedRoutes.map(async (r) => {
        const waypoints = r.stops
          .filter((s) => s.lat && s.lon)
          .map((s) => [s.lat, s.lon]);

        if (destination.lat && destination.lon) {
          waypoints.push([destination.lat, destination.lon]);
        }

        if (waypoints.length >= 2) {
          try {
            const roadData = await fetchRoadRoute(waypoints);
            if (roadData) {
              statsObj[r.bus_id] = {
                distanceKm: roadData.distanceKm,
                coordinates: roadData.coordinates,
              };
            }
          } catch (err) {
            console.warn(`Road route fetch fallback for ${r.bus_id}:`, err);
            statsObj[r.bus_id] = {
              distanceKm: r.total_km,
              coordinates: waypoints,
            };
          }
        }
      });

      await Promise.allSettled(promises);
      if (isMounted) {
        setRoadStatsMap(statsObj);
        setIsLoadingRoads(false);
      }
    };

    fetchAll();

    return () => {
      isMounted = false;
    };
  }, [displayedRoutes, destination]);

  // Render Map layers (All routes or focused route)
  useEffect(() => {
    if (!mapRef.current) return;

    // Initialize Map once
    if (!mapInstanceRef.current) {
      const firstRouteStop = displayedRoutes
        .flatMap((route) => route.stops || [])
        .find((stop) => Number.isFinite(Number(stop.lat)) && Number.isFinite(Number(stop.lon)));
      const initialCenter = Number.isFinite(Number(destination?.lat)) && Number.isFinite(Number(destination?.lon))
        ? [Number(destination.lat), Number(destination.lon)]
        : firstRouteStop
          ? [Number(firstRouteStop.lat), Number(firstRouteStop.lon)]
          : null;
      if (!initialCenter) return;

      mapInstanceRef.current = L.map(mapRef.current, {
        zoomControl: true,
      }).setView(initialCenter, 11);

      // OpenStreetMap basemap — free, no API key required
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; <a href=\"https://www.openstreetmap.org/copyright\">OpenStreetMap</a> contributors",
        subdomains: "abc",
        maxZoom: 19,
      }).addTo(mapInstanceRef.current);
    }

    const map = mapInstanceRef.current;

    // Clear existing layers
    layersRef.current.forEach((layer) => map.removeLayer(layer));
    layersRef.current = [];

    const allBounds = [];

    // 1. Plot Plant Destination Drop Point
    if (destination && destination.lat && destination.lon) {
      const destPt = [destination.lat, destination.lon];
      allBounds.push(destPt);

      const destIcon = L.divIcon({
        className: "dest-marker",
        html: `
          <div style="
            background: #E5607A;
            color: #fff;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 3px solid #ffffff;
            box-shadow: 0 4px 12px rgba(229, 96, 122, 0.45);
          ">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/>
              <circle cx="12" cy="10" r="3"/>
            </svg>
          </div>
        `,
        iconSize: [32, 32],
        iconAnchor: [16, 16],
      });

      const destMarker = L.marker(destPt, { icon: destIcon, zIndexOffset: 1000 }).bindPopup(`
        <div style="font-family:Inter,sans-serif; font-size:12px; line-height:1.4;">
          <div style="font-weight:800; color:#B8415A; font-size:13px; margin-bottom:2px;">
            ${destination.name || "Company Plant"}
          </div>
          <div style="color:#0F172A; font-weight:600;">Main Drop Location</div>
          <div style="color:#64748B; font-size:11px;">${destination.lat}, ${destination.lon}</div>
        </div>
      `);
      destMarker.addTo(map);
      layersRef.current.push(destMarker);
    }

    // 2. Render Routes & Stops
    const isAllMode = selectedBusIds.length === 0;

    displayedRoutes.forEach((route) => {
      const color = getBusColor(route.bus_id);
      const isFocused = !isAllMode && selectedBusIds.includes(route.bus_id);
      const isDimmed = !isAllMode && !isFocused;

      const rawWaypoints = route.stops
        .filter((s) => s.lat && s.lon)
        .map((s) => [s.lat, s.lon]);

      if (destination && destination.lat && destination.lon) {
        rawWaypoints.push([destination.lat, destination.lon]);
      }

      // Prioritize backend OSRM turn-by-turn road geometry if available
      const cachedRoad = roadStatsMap[route.bus_id];
      const polylinePoints =
        route.road_geometry && route.road_geometry.length >= 2
          ? route.road_geometry
          : cachedRoad && cachedRoad.coordinates
          ? cachedRoad.coordinates
          : rawWaypoints;

      if (polylinePoints.length >= 2) {
        // Collect bounds only for focused routes (or all if no filter)
        if (!isDimmed) {
          polylinePoints.forEach((pt) => allBounds.push(pt));
        }

        // Casing polyline (white border for contrast)
        const casing = L.polyline(polylinePoints, {
          color: "#FFFFFF",
          weight: isFocused ? 8 : isDimmed ? 2 : 6,
          opacity: isDimmed ? 0.08 : 0.9,
          lineCap: "round",
          lineJoin: "round",
        }).addTo(map);
        layersRef.current.push(casing);

        // Core route line
        const routeLine = L.polyline(polylinePoints, {
          color: color,
          weight: isFocused ? 5.5 : isDimmed ? 1.5 : 4,
          opacity: isDimmed ? 0.15 : 0.95,
          lineCap: "round",
          lineJoin: "round",
        }).addTo(map);

        // Route line click toggles selection
        routeLine.on("click", () => {
          setSelectedStopData(null);
          const next = selectedBusIds.includes(route.bus_id)
            ? selectedBusIds.filter((id) => id !== route.bus_id)
            : [...selectedBusIds, route.bus_id];
          setSelectedBusIds(next);
          if (onSelectBus) {
            onSelectBus(next.length === 1 ? next[0] : next.length > 1 ? next.join(",") : null);
          }
        });

        layersRef.current.push(routeLine);
      }

      // Render Pickup Stop Markers (hide or faint if dimmed)
      if (!isDimmed) {
        route.stops.forEach((s) => {
          if (!s.lat || !s.lon) return;
          const pt = [s.lat, s.lon];

          const isHoveredStop = hoveredEmployee && hoveredEmployee.pickup_point === s.stop_name;
          const isSelectedStop = selectedStopData && selectedStopData.stop_name === s.stop_name && selectedStopData.bus_id === route.bus_id;

          const employeeList = s.employees || [];
          const employeeNamesHtml = employeeList
            .map(
              (e) =>
                `<div style="margin-top:3px; padding-left:4px; border-left:2px solid ${color};">
                  <b>${e.employee_name || e.emp_name || "Employee"}</b> (${e.shift || route.shift})<br/>
                  <span style="color:#64748B;">ID: ${e.employee_id || e.sr_no || "-"} | Mob: ${e.mobile || "-"}</span>
                </div>`
            )
            .join("");

          const markerIcon = L.divIcon({
            className: "custom-stop-marker",
            html: `
              <div style="
                background: ${isSelectedStop ? "#E5607A" : isHoveredStop ? "#E39A4B" : color};
                color: #ffffff;
                width: ${isFocused || isSelectedStop ? "28px" : "24px"};
                height: ${isFocused || isSelectedStop ? "28px" : "24px"};
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 11px;
                font-weight: 800;
                border: 2px solid #ffffff;
                box-shadow: 0 2px 8px rgba(31,42,68,0.28);
                transition: transform 0.2s;
              ">
                ${s.seq}
              </div>
            `,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
          });

          const marker = L.marker(pt, {
            icon: markerIcon,
            zIndexOffset: isFocused || isSelectedStop ? 500 : 0,
          }).bindPopup(`
            <div style="font-family:Inter,sans-serif; font-size:12px; line-height: 1.4; min-width: 220px; max-width: 280px;">
              <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                <span style="font-size:13px; font-weight:800; color:${color};">
                  ${route.bus_id} (${route.shift})
                </span>
                <span style="background:${color}15; color:${color}; padding:2px 6px; border-radius:4px; font-weight:700; font-size:11px;">
                  Stop #${s.seq}
                </span>
              </div>
              <div style="font-weight:700; color:#0F172A; font-size:12px; margin-bottom:4px;">
                ${s.stop_name}
              </div>
              <div style="color:#475569; font-size:11px;">
                <b>Distance from prev stop:</b> ${s.distance_from_prev_km > 0 ? `+${s.distance_from_prev_km} km` : "Start (First stop)"}<br/>
                <b>Distance to plant:</b> ${s.distance_to_destination_km ?? "—"} km
              </div>
              <div style="margin-top:8px; padding-top:6px; border-top:1px solid #E6EAF2; font-size:11px;">
                <b style="color:#1E293B;">Employees Boarding Here (${employeeList.length}):</b>
                <div style="max-height:130px; overflow-y:auto; margin-top:4px;">
                  ${employeeNamesHtml}
                </div>
              </div>
            </div>
          `);

          // Clicking stop selects both the bus and opens stop details panel
          marker.on("click", () => {
            setSelectedStopData({
              ...s,
              bus_id: route.bus_id,
              shift: route.shift,
              color,
              employees: employeeList,
            });
            if (!selectedBusIds.includes(route.bus_id)) {
              setSelectedBusIds([route.bus_id]);
              if (onSelectBus) onSelectBus(route.bus_id);
            }
          });

          marker.addTo(map);
          layersRef.current.push(marker);
        });
      }
    });

    // Fit map bounds to show active markers/routes
    if (allBounds.length > 0) {
      map.fitBounds(allBounds, { padding: [40, 40], maxZoom: 14 });
    }

    setTimeout(() => {
      map.invalidateSize();
    }, 200);
  }, [displayedRoutes, destination, selectedBusIds, roadStatsMap, hoveredEmployee, selectedStopData]);

  const clearSelection = () => {
    setSelectedBusIds([]);
    setSelectedStopData(null);
    if (onSelectBus) onSelectBus(null);
  };

  const toggleBus = (busId) => {
    setSelectedStopData(null);
    const next = selectedBusIds.includes(busId)
      ? selectedBusIds.filter((id) => id !== busId)
      : [...selectedBusIds, busId];
    setSelectedBusIds(next);
    if (onSelectBus) {
      onSelectBus(next.length === 1 ? next[0] : next.length > 1 ? next.join(",") : null);
    }
  };

  return (
    <div className="route-map-shell">
      {/* Filters */}
      <div className="filter-bar">
        <div className="filter-group">
          <span className="filter-label">Shift</span>
          <div className="seg">
            {["ALL", "A", "B", "C"].map((sh) => (
              <button
                key={sh}
                className={`seg-btn ${selectedShift === sh ? "active" : ""}`}
                onClick={() => {
                  setSelectedShift(sh);
                  setSelectedBusIds([]);
                  if (onSelectBus) onSelectBus(null);
                }}
              >
                {sh === "ALL" ? "All" : sh}
              </button>
            ))}
          </div>
        </div>

        <div className="filter-group">
          <span className="filter-label">Corridor</span>
          <select
            className="select-field"
            value={selectedCorridor}
            onChange={(e) => {
              setSelectedCorridor(e.target.value);
              setSelectedBusIds([]);
              if (onSelectBus) onSelectBus(null);
            }}
          >
            {corridors.map((c) => (
              <option key={c} value={c}>{c === "ALL" ? "All corridors" : c}</option>
            ))}
          </select>
        </div>

        {selectedBusIds.length > 0 && (
          <button className="btn btn-ghost btn-sm" onClick={clearSelection}>Show all routes</button>
        )}

        <div className="filter-legend">
          {isLoadingRoads && (
            <span className="routing-status"><span className="spinner" /> Routing roads…</span>
          )}
          <span className="legend-item">
            <span className="legend-dot" style={{ background: "#E5607A" }} /> Drop location
          </span>
          <span className="legend-item">{displayedRoutes.length} {displayedRoutes.length === 1 ? "route" : "routes"}</span>
        </div>
      </div>

      {/* Comparison */}
      {selectedBusIds.length >= 2 && (
        <div className="compare-bar">
          <div className="compare-left">
            <span className="compare-title">Comparing {selectedBusIds.length} routes</span>
            {selectedBusIds.map((id) => {
              const r = routes.find((rt) => rt.bus_id === id);
              return (
                <span key={id} className="compare-chip" style={{ "--chip": getBusColor(id) }}>
                  <span className="legend-dot" style={{ background: getBusColor(id) }} />
                  {id}
                  {r && <em>{r.passengers_assigned} pax</em>}
                  <button
                    type="button"
                    title={`Remove ${id}`}
                    aria-label={`Remove ${id}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      const next = selectedBusIds.filter((b) => b !== id);
                      setSelectedBusIds(next);
                      if (onSelectBus) onSelectBus(next.length === 1 ? next[0] : null);
                    }}
                  >
                    <X size={12} />
                  </button>
                </span>
              );
            })}
          </div>
          <div className="compare-right">
            <span>
              Combined pax: <b>{selectedBusIds.reduce((sum, id) => sum + (routes.find((rt) => rt.bus_id === id)?.passengers_assigned || 0), 0)}</b>
            </span>
            <button className="btn btn-ghost btn-sm" onClick={clearSelection}>Reset</button>
          </div>
        </div>
      )}

      <div className="map-split">
        {/* Route list */}
        <div className="route-metrics" role="list" aria-label="Routes">
          {displayedRoutes.map((r) => {
            const color = getBusColor(r.bus_id);
            const stat = roadStatsMap[r.bus_id];
            const isSelected = selectedBusIds.includes(r.bus_id);
            const distanceDisplay = stat?.distanceKm ?? r.total_km;
            // Use the backend's full longest-ride estimate (including stop dwell).
            // The map router's drive-only time is not the passenger ride duration.
            const timeDisplay = r.longest_ride_minutes;

            return (
              <button
                type="button"
                key={r.bus_id}
                role="listitem"
                className={`route-metric ${isSelected ? "selected" : ""}`}
                style={{ "--route-color": color }}
                aria-pressed={isSelected}
                aria-label={`${isSelected ? "Deselect" : "Select"} ${r.shift} route ${r.route_no}`}
                onClick={() => toggleBus(r.bus_id)}
              >
                <div className="route-metric-head">
                  <span className="route-metric-name">
                    <span className="legend-dot" style={{ background: color }} />
                    Route {r.route_no}
                  </span>
                  <span className="route-metric-shift">Shift {String(r.shift).replace("Shift ", "")}</span>
                </div>
                <div className="route-metric-stats">
                  <span className="route-metric-stat"><strong>{r.passengers_assigned}</strong><small>people</small></span>
                  <span className="route-metric-stat"><strong>{distanceDisplay == null ? "—" : Number(distanceDisplay).toFixed(1)}</strong><small>km</small></span>
                  <span className={`route-metric-stat route-metric-time ${Number(timeDisplay) > 70 ? "is-over-limit" : ""}`}>
                    <strong>{timeDisplay == null ? "—" : Math.round(Number(timeDisplay))}</strong><small>min</small>
                  </span>
                </div>
              </button>
            );
          })}
        </div>

        {/* Map */}
        <div className="map-stage">
          <div className="route-map-canvas" ref={mapRef} />

          {selectedStopData && (
            <div className="stop-drawer">
              <div className="stop-drawer-head">
                <div className="stop-drawer-title">
                  <span className="stop-seq" style={{ background: selectedStopData.color }}>{selectedStopData.seq}</span>
                  <div>
                    <strong>{selectedStopData.stop_name}</strong>
                    <span>
                      <b style={{ color: selectedStopData.color }}>{selectedStopData.bus_id}</b>
                      {selectedStopData.pickup_time ? ` · pickup ${selectedStopData.pickup_time}` : ""}
                    </span>
                  </div>
                </div>
                <button type="button" className="icon-btn" onClick={() => setSelectedStopData(null)} aria-label="Close stop details">
                  <X size={16} />
                </button>
              </div>

              <div className="stop-drawer-count">
                Boarding at this stop ({selectedStopData.employees.length})
              </div>

              <div className="emp-grid">
                {selectedStopData.employees.map((emp, i) => (
                  <div key={i} className="emp-card">
                    <strong>{emp.employee_name || emp.emp_name || "Employee"}</strong>
                    <div>
                      <span>ID <b>{emp.employee_id || emp.sr_no || "-"}</b></span>
                      <span>Shift <b>{emp.shift || selectedStopData.shift}</b></span>
                      <span><b>{emp.mobile && emp.mobile !== "nan" ? emp.mobile : "-"}</b></span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
