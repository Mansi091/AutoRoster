import React, { useEffect, useRef, useState, useMemo } from "react";
import L from "leaflet";
import { fetchRoadRoute } from "../utils/routing";
import { X } from "lucide-react";

const ROUTE_COLORS = [
  "#2563EB", // Blue
  "#0D9488", // Teal
  "#EA580C", // Amber
  "#7C3AED", // Violet
  "#E11D48", // Rose
  "#0284C7", // Sky
  "#4F46E5", // Indigo
  "#16A34A", // Emerald
  "#D97706", // Ochre
  "#64748B", // Slate
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
                durationMinutes: roadData.durationMinutes,
                coordinates: roadData.coordinates,
              };
            }
          } catch (err) {
            console.warn(`Road route fetch fallback for ${r.bus_id}:`, err);
            statsObj[r.bus_id] = {
              distanceKm: r.total_km,
              durationMinutes: Math.round(r.total_km * 1.5),
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
      mapInstanceRef.current = L.map(mapRef.current, {
        zoomControl: true,
      }).setView([destination?.lat ?? 18.7765871, destination?.lon ?? 73.6947478], 11);

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
            background: #DC2626;
            color: #fff;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            border: 3px solid #ffffff;
            box-shadow: 0 3px 8px rgba(220, 38, 38, 0.5);
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
        <div style="font-family:sans-serif; font-size:12px; line-height:1.4;">
          <div style="font-weight:800; color:#DC2626; font-size:13px; margin-bottom:2px;">
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

      // Check if road geometry is available in cache
      const cachedRoad = roadStatsMap[route.bus_id];
      const polylinePoints = cachedRoad && cachedRoad.coordinates ? cachedRoad.coordinates : rawWaypoints;

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
          setSelectedBusIds((prev) => {
            let next;
            if (prev.includes(route.bus_id)) {
              next = prev.filter((id) => id !== route.bus_id);
            } else {
              next = [...prev, route.bus_id];
            }
            if (onSelectBus) {
              onSelectBus(next.length === 1 ? next[0] : next.length > 1 ? next.join(",") : null);
            }
            return next;
          });
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
                  <b>${e.employee_name}</b> (${e.shift})<br/>
                  <span style="color:#64748B;">ID: ${e.employee_id || "-"} | Mob: ${e.mobile || "-"}</span>
                </div>`
            )
            .join("");

          const markerIcon = L.divIcon({
            className: "custom-stop-marker",
            html: `
              <div style="
                background: ${isSelectedStop ? "#EF4444" : isHoveredStop ? "#F59E0B" : color};
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
                box-shadow: 0 2px 6px rgba(0,0,0,0.35);
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
            <div style="font-family:sans-serif; font-size:12px; line-height: 1.4; min-width: 220px; max-width: 280px;">
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
                <b>Distance to plant:</b> ${s.distance_to_destination_km} km
              </div>
              <div style="margin-top:8px; padding-top:6px; border-top:1px solid #E2E8F0; font-size:11px;">
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

  return (
    <div style={{ background: "#FFFFFF", padding: 20, borderRadius: "14px", border: "1px solid #E2E8F0", boxShadow: "0 1px 4px rgba(0,0,0,0.05)" }}>
      {/* Top Controls Bar */}
      <div className="filter-bar">
        {/* Shift Filter */}
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

        {/* Corridor Filter */}
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
              <option key={c} value={c}>
                {c === "ALL" ? "All Corridors" : c}
              </option>
            ))}
          </select>
        </div>

        {/* Bus Route Selector */}
        <div className="filter-group">
          <span className="filter-label">Bus</span>
          <select
            className="select-field"
            value={selectedBusIds.length === 1 ? selectedBusIds[0] : selectedBusIds.length > 1 ? "CUSTOM" : "ALL"}
            onChange={(e) => {
              const val = e.target.value;
              setSelectedStopData(null);
              if (val === "ALL") {
                setSelectedBusIds([]);
                if (onSelectBus) onSelectBus(null);
              } else if (val !== "CUSTOM") {
                setSelectedBusIds([val]);
                if (onSelectBus) onSelectBus(val);
              }
            }}
          >
            <option value="ALL">All Routes ({displayedRoutes.length})</option>
            {selectedBusIds.length > 1 && (
              <option value="CUSTOM">
                ★ {selectedBusIds.length} Buses Selected ({selectedBusIds.join(", ")})
              </option>
            )}
            {displayedRoutes.map((r) => (
              <option key={r.bus_id} value={r.bus_id}>
                {r.bus_id} — {r.corridor || "Central"}
              </option>
            ))}
          </select>
        </div>

        {/* Quick "Show All" toggle button */}
        {selectedBusIds.length > 0 && (
          <button
            className="btn btn-ghost"
            onClick={() => {
              setSelectedBusIds([]);
              setSelectedStopData(null);
              if (onSelectBus) onSelectBus(null);
            }}
          >
            Show all routes
          </button>
        )}

        {/* Legend & Routing Status */}
        <div className="filter-legend">
          {isLoadingRoads && (
            <span className="routing-status">
              <span className="spinner" />
              Routing roads…
            </span>
          )}
          <span className="legend-item">
            <span className="legend-dot" style={{ background: "#DC2626" }} />
            Drop location
          </span>
        </div>
      </div>

      {/* Multi-Bus Comparison Banner (Shown when 2 or more buses are selected) */}
      {selectedBusIds.length >= 2 && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexWrap: "wrap",
            gap: 10,
            padding: "10px 14px",
            marginBottom: 12,
            background: "#F0F9FF",
            border: "1px solid #BAE6FD",
            borderRadius: "10px",
            animation: "fadeIn 0.2s ease-in-out",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 800, color: "#0284C7", fontSize: "12px", textTransform: "uppercase", letterSpacing: "0.5px" }}>
              Comparing {selectedBusIds.length} Buses:
            </span>
            {selectedBusIds.map((id) => {
              const r = routes.find((rt) => rt.bus_id === id);
              const c = getBusColor(id);
              return (
                <span
                  key={id}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    padding: "3px 10px",
                    background: "#FFFFFF",
                    border: `2px solid ${c}`,
                    borderRadius: "20px",
                    fontWeight: 700,
                    fontSize: "12px",
                    color: "#0F172A",
                    boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
                  }}
                >
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: c }} />
                  {id}
                  {r && <span style={{ color: "#64748B", fontWeight: 500 }}>({r.passengers_assigned} pax)</span>}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      const next = selectedBusIds.filter((b) => b !== id);
                      setSelectedBusIds(next);
                      if (onSelectBus) onSelectBus(next.length === 1 ? next[0] : null);
                    }}
                    style={{
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      padding: "0 2px",
                      color: "#94A3B8",
                      fontWeight: 800,
                      fontSize: "12px",
                      lineHeight: 1,
                    }}
                    title={`Remove ${id}`}
                  >
                    ✕
                  </button>
                </span>
              );
            })}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
            <span style={{ fontSize: "12px", color: "#0369A1", fontWeight: 600 }}>
              Combined Pax: <b>{
                selectedBusIds.reduce((sum, id) => {
                  const r = routes.find((rt) => rt.bus_id === id);
                  return sum + (r?.passengers_assigned || 0);
                }, 0)
              }</b>
            </span>
            <button
              className="btn btn-ghost"
              style={{ fontSize: "12px", padding: "4px 8px", color: "#0284C7" }}
              onClick={() => {
                setSelectedBusIds([]);
                setSelectedStopData(null);
                if (onSelectBus) onSelectBus(null);
              }}
            >
              Reset to all
            </button>
          </div>
        </div>
      )}

      {/* Route Metric Cards Bar: Click to select / compare multiple buses */}
      <div className="route-metrics">
        {displayedRoutes.map((r) => {
          const color = getBusColor(r.bus_id);
          const stat = roadStatsMap[r.bus_id];
          const isSelected = selectedBusIds.includes(r.bus_id);

          const distanceDisplay = stat ? `${stat.distanceKm} km` : `${r.total_km} km`;
          const timeDisplay = stat ? `~${stat.durationMinutes} min` : "…";

          return (
            <div
              key={r.bus_id}
              className={`route-metric ${isSelected ? "selected" : ""}`}
              style={{
                border: isSelected ? `2px solid ${color}` : undefined,
                boxShadow: isSelected ? `0 0 0 3px ${color}20` : undefined,
                position: "relative",
              }}
              onClick={() => {
                setSelectedStopData(null);
                setSelectedBusIds((prev) => {
                  let next;
                  if (prev.includes(r.bus_id)) {
                    // Deselect
                    next = prev.filter((id) => id !== r.bus_id);
                  } else {
                    // Add to multi-selection
                    next = [...prev, r.bus_id];
                  }
                  if (onSelectBus) {
                    onSelectBus(next.length === 1 ? next[0] : next.length > 1 ? next.join(",") : null);
                  }
                  return next;
                });
              }}
              title={`Click to toggle ${r.bus_id} · Select multiple buses to compare side-by-side`}
            >
              <div className="route-metric-head">
                <span className="route-metric-name">
                  <span className="legend-dot" style={{ background: color }} />
                  {r.bus_id}
                </span>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span className="route-metric-shift">{r.shift.replace("Shift ", "S")}</span>
                  {/* Subtle checkbox indicator */}
                  <div
                    style={{
                      width: 15,
                      height: 15,
                      borderRadius: 3,
                      border: isSelected ? `2px solid ${color}` : "1.5px solid #CBD5E1",
                      background: isSelected ? color : "transparent",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      color: "#FFFFFF",
                      fontSize: "9px",
                      fontWeight: 900,
                      transition: "all 0.15s ease",
                    }}
                  >
                    {isSelected && "✓"}
                  </div>
                </div>
              </div>
              <div style={{ fontSize: "11px", color: "var(--text-muted)", marginBottom: "4px", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {r.corridor || "Local"}
              </div>
              <div className="route-metric-stats">
                <span>{r.passengers_assigned} pax</span>
                <span className="sep">·</span>
                <span>{distanceDisplay}</span>
                <span className="sep">·</span>
                <span className="time">{timeDisplay}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* The Leaflet Map Canvas */}
      <div
        ref={mapRef}
        style={{
          height: "min(560px, 65vh)",
          minHeight: "360px",
          width: "100%",
          borderRadius: "12px",
          overflow: "hidden",
          border: "1px solid var(--border-subtle)",
        }}
      />

      {/* Selected Stop & Assigned Employee Details Drawer */}
      {selectedStopData && (
        <div
          style={{
            marginTop: 14,
            padding: "14px 18px",
            background: "#F8FAFC",
            borderRadius: "10px",
            border: `1px solid ${selectedStopData.color}40`,
            boxShadow: "0 1px 3px rgba(0,0,0,0.04)",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div
                style={{
                  background: selectedStopData.color,
                  color: "#fff",
                  width: 24,
                  height: 24,
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: "11px",
                  fontWeight: 800,
                }}
              >
                {selectedStopData.seq}
              </div>
              <div>
                <span style={{ fontWeight: 800, color: "#0F172A", fontSize: "0.95rem" }}>
                  {selectedStopData.stop_name}
                </span>
                <span style={{ marginLeft: 10, fontSize: "0.8rem", color: "#64748B" }}>
                  Assigned to <b style={{ color: selectedStopData.color }}>{selectedStopData.bus_id}</b> ({selectedStopData.shift})
                </span>
              </div>
            </div>
            <button
              onClick={() => setSelectedStopData(null)}
              style={{ background: "transparent", border: "none", cursor: "pointer", color: "#64748B" }}
            >
              <X size={16} />
            </button>
          </div>

          <div style={{ fontSize: "0.82rem", fontWeight: 700, color: "#334155", marginBottom: 6 }}>
            Employees Boarding at this Stop ({selectedStopData.employees.length}):
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
            {selectedStopData.employees.map((emp, i) => (
              <div
                key={i}
                style={{
                  background: "#FFFFFF",
                  padding: "10px 14px",
                  borderRadius: "8px",
                  border: "1px solid #E2E8F0",
                }}
              >
                <div style={{ fontWeight: 800, color: "#0F172A", fontSize: "0.88rem" }}>
                  {emp.employee_name}
                </div>
                <div style={{ fontSize: "0.78rem", color: "#64748B", marginTop: 2, display: "flex", justifyContent: "space-between" }}>
                  <span>ID: <b>{emp.employee_id || "-"}</b></span>
                  <span>Shift: <b>{emp.shift}</b></span>
                  <span>Mob: <b>{emp.mobile || "-"}</b></span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
