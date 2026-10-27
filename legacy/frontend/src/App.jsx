import React, { useState, useMemo, useRef, useEffect } from "react";
import {
  Bus,
  Upload,
  Download,
  MapPin,
  Users,
  Crosshair,
  Settings,
  Check,
  CheckCircle2,
  FileText,
  AlertTriangle,
  RefreshCw,
  X,
  Menu,
  Compass,
  Search,
} from "lucide-react";
import { parseExcelFile, exportToExcel } from "./utils/excel";
import { clusterStopsIntoRoutes, DEFAULT_DESTINATION, resolveCoordinates } from "./utils/geo";
// buildRouteMatrix no longer needed — OR-Tools backend handles road matrix
import { computeFleetCost } from "./utils/fleetCost";
import { exportRoutePdf } from "./utils/routePdf";
import RouteMap from "./components/RouteMap";

async function withVerifiedCoordinates(employees) {
  const missingStops = new Set();
  const locatedEmployees = employees.map((employee) => {
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
      missingStops.add(employee.pickup_point);
      return {
        ...employee,
        latitude: null,
        longitude: null,
        location_status: "LOCATION_UNVERIFIED",
      };
    }
    return {
      ...employee,
      latitude: coordinates[0],
      longitude: coordinates[1],
      location_status: "VERIFIED",
    };
  });

  if (!missingStops.size) return locatedEmployees;

  try {
    const response = await fetch("/api/resolve_pickups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pickup_points: [...missingStops] }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || `Location resolver returned HTTP ${response.status}.`);
    }

    const { locations } = await response.json();
    return locatedEmployees.map((employee) => {
      if (employee.location_status === "VERIFIED") return employee;
      const location = locations?.[employee.pickup_point];
      const coordinates =
        location?.location_status === "VERIFIED"
          ? resolveCoordinates(
              employee.pickup_point,
              location.latitude,
              location.longitude,
              true
            )
          : null;
      if (coordinates) {
        return {
          ...employee,
          latitude: coordinates[0],
          longitude: coordinates[1],
          location_status: "VERIFIED",
          coordinate_source: location.coordinate_source,
          canonical_pickup_point: location.canonical_name,
        };
      }
      return {
        ...employee,
        location_status: "LOCATION_UNVERIFIED",
        resolution_reason:
          location?.resolution_reason || "No verified location could be resolved.",
      };
    });
  } catch (error) {
    console.warn("Pickup geocoding unavailable; unresolved employees will not be routed:", error);
    return locatedEmployees.map((employee) =>
      employee.location_status === "VERIFIED"
        ? employee
        : {
            ...employee,
            location_status: "LOCATION_UNVERIFIED",
            resolution_reason: `Location resolver unavailable: ${
              error instanceof Error ? error.message : String(error)
            }`,
          }
    );
  }
}

export default function App() {
  const [employees, setEmployees] = useState([]);
  const [fileMeta, setFileMeta] = useState(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const groupByShift = true;
  const [activeTab, setActiveTab] = useState("employees"); // "routes" | "employees" | "map"
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [shiftFilter, setShiftFilter] = useState("ALL");
  const [corridorFilter, setCorridorFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [isDragOver, setIsDragOver] = useState(false);
  const fileInputRef = useRef(null);
  const dataLoadRequestRef = useRef(0);

  // Single Drop Point Destination
  const [destination, setDestination] = useState(DEFAULT_DESTINATION);
  const [showDestModal, setShowDestModal] = useState(false);
  const [destInputName, setDestInputName] = useState(DEFAULT_DESTINATION.name);
  const [destInputLat, setDestInputLat] = useState(String(DEFAULT_DESTINATION.lat));
  const [destInputLon, setDestInputLon] = useState(String(DEFAULT_DESTINATION.lon));

  // Location Review & Auto-Fix States
  const [showReviewModal, setShowReviewModal] = useState(false);
  const [isAutoResolving, setIsAutoResolving] = useState(false);
  const [autoResolveStatus, setAutoResolveStatus] = useState("");
  const [employeeStatusFilter, setEmployeeStatusFilter] = useState("ALL"); // "ALL" | "UNVERIFIED"
  const [manualCoordsInput, setManualCoordsInput] = useState({});
  const [expandedCandidates, setExpandedCandidates] = useState({});
  const [loadingCandidates, setLoadingCandidates] = useState({});

  // Active / Hovered employee or bus for interactive inspection
  const [hoveredEmployee, setHoveredEmployee] = useState(null);
  const [selectedBusId, setSelectedBusId] = useState(null);

  // Group unverified employees by unique pickup point
  const unverifiedStopsSummary = useMemo(() => {
    if (!employees.length) return [];
    const map = new Map();
    employees.forEach((e) => {
      if (e.location_status === "LOCATION_UNVERIFIED") {
        const stop = e.pickup_point;
        if (!map.has(stop)) {
          map.set(stop, {
            stop_name: stop,
            employees: [],
            reason: e.resolution_reason || "Unverified coordinates",
          });
        }
        map.get(stop).employees.push(e);
      }
    });
    return Array.from(map.values());
  }, [employees]);

  // One-Click AI Auto-Resolve using Gemini transit supervisor
  const handleAgenticAutoResolve = async (specificStops = null) => {
    const stopsToResolve = specificStops || unverifiedStopsSummary.map((s) => s.stop_name);
    if (!stopsToResolve.length) return;

    setIsAutoResolving(true);
    setAutoResolveStatus(`AI Agent is evaluating ${stopsToResolve.length} stops against factory transit rules...`);

    try {
      const knownCoords = employees
        .filter((e) => e.location_status === "VERIFIED" && Number.isFinite(e.latitude) && Number.isFinite(e.longitude))
        .map((e) => [e.latitude, e.longitude]);

      const res = await fetch("/api/agentic_resolve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stops: stopsToResolve, known_coords: knownCoords }),
      });
      const resText = await res.text();
      let data;
      try {
        data = JSON.parse(resText);
      } catch (parseErr) {
        throw new Error(`Server returned HTTP ${res.status}: ${resText.slice(0, 150) || "No response body"}`);
      }
      if (!res.ok || data.status === "ERROR") {
        throw new Error(data.message || data.error || "Failed to auto-resolve.");
      }

      setAutoResolveStatus("Coordinates verified! Updating routes & buses...");
      // Re-run resolution for employees
      const resetEmployees = employees.map((e) => ({
        ...e,
        location_status: undefined,
      }));
      const updated = await withVerifiedCoordinates(resetEmployees);
      setEmployees(updated);
      setFileMeta((prev) => prev ? {
        ...prev,
        unverifiedCount: updated.filter((e) => e.location_status === "LOCATION_UNVERIFIED").length,
      } : null);

      setAutoResolveStatus("✅ All locations verified and saved!");
      setTimeout(() => {
        setAutoResolveStatus("");
      }, 3500);
    } catch (err) {
      console.error("Agentic resolve failed:", err);
      setAutoResolveStatus(`❌ Error: ${err.message}`);
    } finally {
      setIsAutoResolving(false);
    }
  };

  const handleFetchCandidates = async (stopName) => {
    if (expandedCandidates[stopName]) {
      // Toggle close
      setExpandedCandidates((prev) => {
        const next = { ...prev };
        delete next[stopName];
        return next;
      });
      return;
    }

    setLoadingCandidates((prev) => ({ ...prev, [stopName]: true }));
    try {
      const knownCoords = employees
        .filter((e) => e.location_status === "VERIFIED" && Number.isFinite(e.latitude) && Number.isFinite(e.longitude))
        .map((e) => [e.latitude, e.longitude]);

      const res = await fetch("/api/fetch_candidates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stop_name: stopName, known_coords: knownCoords }),
      });
      const data = await res.json();
      setExpandedCandidates((prev) => ({ ...prev, [stopName]: data.candidates || [] }));
    } catch (err) {
      alert(`Could not fetch candidates: ${err.message}`);
    } finally {
      setLoadingCandidates((prev) => ({ ...prev, [stopName]: false }));
    }
  };

  const handleSelectCandidate = async (stopName, cand) => {
    try {
      const res = await fetch("/api/save_coordinate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          stop_name: stopName,
          lat: cand.lat,
          lon: cand.lon,
        }),
      });
      const data = await res.json();
      if (!res.ok || data.status === "ERROR") throw new Error(data.error || "Save failed");

      // Reload coordinates
      const resetEmployees = employees.map((e) => ({ ...e, location_status: undefined }));
      const updated = await withVerifiedCoordinates(resetEmployees);
      setEmployees(updated);
      setFileMeta((prev) => prev ? {
        ...prev,
        unverifiedCount: updated.filter((e) => e.location_status === "LOCATION_UNVERIFIED").length,
      } : null);

      setExpandedCandidates((prev) => {
        const next = { ...prev };
        delete next[stopName];
        return next;
      });
    } catch (err) {
      alert(`Could not apply candidate: ${err.message}`);
    }
  };

  const handleManualSaveCoordinate = async (stopName) => {
    const input = manualCoordsInput[stopName];
    if (!input || !input.lat || !input.lon) {
      alert("Please enter both latitude and longitude.");
      return;
    }

    try {
      const res = await fetch("/api/save_coordinate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          stop_name: stopName,
          lat: parseFloat(input.lat),
          lon: parseFloat(input.lon),
        }),
      });
      const data = await res.json();
      if (!res.ok || data.status === "ERROR") {
        throw new Error(data.error || "Failed to save.");
      }

      const resetEmployees = employees.map((e) => ({
        ...e,
        location_status: undefined,
      }));
      const updated = await withVerifiedCoordinates(resetEmployees);
      setEmployees(updated);
      setFileMeta((prev) => prev ? {
        ...prev,
        unverifiedCount: updated.filter((e) => e.location_status === "LOCATION_UNVERIFIED").length,
      } : null);

      alert(`Saved ${stopName}! Coordinates: [${input.lat}, ${input.lon}]`);
    } catch (err) {
      alert(`Could not save: ${err.message}`);
    }
  };

  // OR-Tools optimized routes (replaces greedy clustering for display)
  const [orToolsRoutes, setOrToolsRoutes]   = useState(null);
  const [isOptimising, setIsOptimising]     = useState(false);
  const [roadRoutingError, setRoadRoutingError] = useState(null);

  // Greedy fallback — used only while OR-Tools is loading or if it fails
  const greedyClustering = useMemo(() => {
    if (!employees.length) return null;
    return clusterStopsIntoRoutes(employees, { groupByShift, destination });
  }, [employees, groupByShift, destination]);

  // The clustering shown to the user: OR-Tools result if available, greedy otherwise
  const clustering = useMemo(() => {
    if (orToolsRoutes) return orToolsRoutes;
    return greedyClustering;
  }, [orToolsRoutes, greedyClustering]);

  // After employees load, call OR-Tools backend for optimized routes.
  // Falls back to greedy clusterStopsIntoRoutes if backend is unavailable.
  useEffect(() => {
    if (!employees.length) {
      setOrToolsRoutes(null);
      return;
    }
    let cancelled = false;
    setIsOptimising(true);
    setRoadRoutingError(null);
    setOrToolsRoutes(null);

    (async () => {
      try {
        const res = await fetch("/api/optimize_all", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            employees: employees.map((e) => ({
              employee_id:     e.employee_id || "",
              employee_name:   e.employee_name || "",
              grade:           e.grade || "",
              shift:           e.shift || "A",
              pickup_point:    e.pickup_point || "",
              mobile:          e.mobile || "",
              latitude:        e.latitude,
              longitude:       e.longitude,
              location_status: e.location_status || "VERIFIED",
            })),
            destination,
            bus_capacity: 32,
          }),
        });
        if (!res.ok) throw new Error(`OR-Tools backend returned HTTP ${res.status}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        if (!cancelled && data.routes?.length) {
          // Shape it like clusterStopsIntoRoutes output
          setOrToolsRoutes({
            routes:               data.routes,
            employees:            data.employees || [],
            routed_employees:     data.routed_employees,
            unverified_employees: data.unverified_employees,
            total_buses:          data.total_buses,
            total_stops:          data.total_stops || data.routes.reduce((s, r) => s + r.total_stops, 0),
            total_fleet_km:       data.total_fleet_km || Number(data.routes.reduce((s, r) => s + r.total_km, 0).toFixed(1)),
            destination,
          });
        }
      } catch (err) {
        console.warn("OR-Tools unavailable, using greedy fallback:", err);
        if (!cancelled) setRoadRoutingError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setIsOptimising(false);
      }
    })();

    return () => { cancelled = true; };
  }, [employees, destination]);

  // Fleet cost summary (diesel + fixed cost per bus) for the current plan.
  const fleetCost = useMemo(() => {
    if (!clustering) return null;
    return computeFleetCost(clustering.routes, {
      totalEmployees: clustering.routed_employees,
      capacity: 32,
    });
  }, [clustering]);

  // Handle file drop/upload
  const handleFileUpload = async (file) => {
    const requestId = ++dataLoadRequestRef.current;
    setIsUploading(true);
    setUploadError("");
    try {
      const parsed = await parseExcelFile(file);
      const locatedEmployees = await withVerifiedCoordinates(parsed.employees);
      if (requestId !== dataLoadRequestRef.current) return;
      setFileMeta({
        name: file.name,
        sheetName: parsed.sheetName,
        totalRows: parsed.totalRowsRead,
        validCount: parsed.validEmployees,
        unverifiedCount: locatedEmployees.filter(
          (employee) => employee.location_status === "LOCATION_UNVERIFIED"
        ).length,
        detectedMapping: parsed.detectedMapping,
      });
      setEmployees(locatedEmployees);
      setSearchQuery("");
      setActiveTab("employees");
    } catch (err) {
      if (requestId === dataLoadRequestRef.current) {
        setUploadError(
          `Could not load ${file.name}: ${
            err instanceof Error ? err.message : String(err)
          }`
        );
      }
    } finally {
      if (requestId === dataLoadRequestRef.current) setIsUploading(false);
    }
  };

  // Load sample dataset
  const handleLoadSample = async () => {
    const requestId = ++dataLoadRequestRef.current;
    setIsUploading(true);
    setUploadError("");
    try {
      const res = await fetch("/api/load_current");
      if (!res.ok) throw new Error(`Sample roster request returned HTTP ${res.status}.`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      if (Array.isArray(data.employees) && data.employees.length && requestId === dataLoadRequestRef.current) {
        const locatedEmployees = await withVerifiedCoordinates(data.employees.map((e) => ({
          employee_id: e.employee_id,
          employee_name: e.employee_name,
          grade: e.grade || "",
          shift: e.shift,
          pickup_point: e.pickup_point,
          location_context: e.area || e.location_context || "",
          mobile: e.mobile,
          latitude: e.latitude,
          longitude: e.longitude,
          location_status: e.location_status,
          resolution_reason: e.resolution_reason,
        })));
        if (requestId !== dataLoadRequestRef.current) return;
        setFileMeta({
          name: "Sample_Company_Employees.xlsx",
          totalRows: data.total_employees,
          validCount: data.total_employees,
          unverifiedCount: locatedEmployees.filter(
            (employee) => employee.location_status === "LOCATION_UNVERIFIED"
          ).length,
          detectedMapping: { name: "Name", shift: "Shift", pickup_point: "Bus Stop" },
        });
        setEmployees(locatedEmployees);
        setSearchQuery("");
        setActiveTab("employees");
      } else if (requestId === dataLoadRequestRef.current) {
        throw new Error("The sample roster did not contain any employees.");
      }
    } catch (err) {
      if (requestId === dataLoadRequestRef.current) {
        setUploadError(
          `Could not load sample roster: ${
            err instanceof Error ? err.message : String(err)
          }`
        );
      }
    } finally {
      if (requestId === dataLoadRequestRef.current) setIsUploading(false);
    }
  };

  const handleExport = () => {
    if (!clustering) return;
    exportToExcel(clustering.routes, clustering.employees, "Grouped_Bus_Roster.xlsx", {
      title: fileMeta?.name || "Transport Roster",
    });
  };

  // Download a driver-facing route sheet for one bus: every pickup stop in
  // order, the leg to the next stop, and who boards where.
  const handleRoutePdf = (route) => {
    const busCost = fleetCost?.per_bus?.find((b) => b.bus_id === route.bus_id);
    exportRoutePdf(route, {
      destination,
      roadStats: busCost
        ? {
            distanceKm: busCost.one_way_km,
            durationMinutes: Math.round(route.total_minutes || 0),
          }
        : null,
    });
  };

  const handleSaveDestination = (e) => {
    e.preventDefault();
    const lat = parseFloat(destInputLat);
    const lon = parseFloat(destInputLon);
    if (isNaN(lat) || isNaN(lon)) {
      alert("Please enter valid latitude and longitude numbers.");
      return;
    }
    setDestination({
      name: destInputName.trim() || "Drop Location",
      lat,
      lon,
    });
    setShowDestModal(false);
  };


  // Filtered employees for table
  const filteredEmployees = useMemo(() => {
    if (!clustering) return [];
    let list = clustering.employees;
    if (shiftFilter !== "ALL") {
      list = list.filter((e) => e.shift && e.shift.toUpperCase().includes(shiftFilter));
    }
    if (employeeStatusFilter === "UNVERIFIED") {
      list = list.filter((e) => e.location_status === "LOCATION_UNVERIFIED" || e.assigned_bus === "Unassigned");
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter(
        (e) =>
          e.employee_name.toLowerCase().includes(q) ||
          e.pickup_point.toLowerCase().includes(q) ||
          e.assigned_bus.toLowerCase().includes(q) ||
          (e.mobile && String(e.mobile).includes(q))
      );
    }
    return list;
  }, [clustering, shiftFilter, employeeStatusFilter, searchQuery]);

  return (
    <div className="app-layout">
      {/* Hidden file input */}
      <input
        type="file"
        ref={fileInputRef}
        accept=".xlsx, .xls"
        style={{ display: "none" }}
        onChange={(e) => {
          const file = e.currentTarget.files?.[0];
          e.currentTarget.value = "";
          if (file) void handleFileUpload(file);
        }}
      />

      {/* Mobile Top Navigation */}
      <header className="mobile-header">
        <div className="mobile-brand">
          <Bus size={20} color="var(--primary)" />
          <span>Autoroster</span>
        </div>
        <button
          type="button"
          className="mobile-menu-btn"
          onClick={() => setMobileMenuOpen((prev) => !prev)}
          aria-label="Toggle navigation menu"
        >
          {mobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
        </button>
      </header>

      {/* Sidebar Backdrop Overlay on Mobile */}
      <div
        className={`sidebar-backdrop ${mobileMenuOpen ? "open" : ""}`}
        onClick={() => setMobileMenuOpen(false)}
      />

      {/* Sidebar Column */}
      <aside className={`sidebar ${mobileMenuOpen ? "open" : ""}`}>
        <div className="sidebar-brand">
          <h1 className="brand-title-simple">
            <Bus size={20} />
            <span>Autoroster</span>
          </h1>
          <button
            type="button"
            className="sidebar-close-btn"
            onClick={() => setMobileMenuOpen(false)}
            aria-label="Close sidebar"
          >
            <X size={18} />
          </button>
        </div>
        <nav className="sidebar-nav">
          <button
            className={`sidebar-tab-btn ${activeTab === "routes" ? "active" : ""}`}
            onClick={() => {
              setActiveTab("routes");
              setMobileMenuOpen(false);
            }}
          >
            <Bus size={18} />
            <span>Bus Routes</span>
            {clustering && <span className="tab-badge">{clustering.routes.length}</span>}
          </button>
          <button
            className={`sidebar-tab-btn ${activeTab === "employees" ? "active" : ""}`}
            onClick={() => {
              setActiveTab("employees");
              setMobileMenuOpen(false);
            }}
          >
            <Users size={18} />
            <span>Employees</span>
            {clustering && <span className="tab-badge">{clustering.employees.length}</span>}
          </button>
          <button
            className={`sidebar-tab-btn ${activeTab === "map" ? "active" : ""}`}
            onClick={() => {
              setActiveTab("map");
              setMobileMenuOpen(false);
            }}
          >
            <MapPin size={18} />
            <span>Route Map</span>
          </button>
        </nav>
        <div className="sidebar-footer">
          <button
            className="btn btn-primary"
            style={{ width: "100%", justifyContent: "center" }}
            onClick={handleExport}
            disabled={!clustering}
          >
            <Download size={16} />
            <span>Export Excel</span>
          </button>
        </div>
      </aside>

      <main className="main-content">
        <div className="top-action-bar">
          <div className="top-action-left">
            <h2 className="page-title">
              {activeTab === "routes" && "Bus Routes"}
              {activeTab === "employees" && "Employees"}
              {activeTab === "map" && "Route Map"}
            </h2>
            <div className="workbook-file-meta">
              {fileMeta ? (
                <>
                  <strong>{fileMeta.name}</strong>
                  <span>
                    {fileMeta.validCount} employee rows loaded
                    {fileMeta.sheetName ? ` from "${fileMeta.sheetName}"` : ""}
                  </span>
                </>
              ) : (
                <span>No workbook loaded. Upload an Excel roster to begin.</span>
              )}
              {isUploading && (
                <span className="workbook-processing" role="status">
                  <span className="spinner" /> Reading and processing workbook…
                </span>
              )}
              {fileMeta?.unverifiedCount > 0 && (
                <button
                  type="button"
                  onClick={() => setShowReviewModal(true)}
                  className="workbook-warning"
                  title="Click to view all unverified locations and fix them"
                >
                  <AlertTriangle size={14} />
                  <span>{fileMeta.unverifiedCount} pickup location(s) need review — Verify Locations</span>
                </button>
              )}
              {uploadError && (
                <span className="workbook-error" role="alert">{uploadError}</span>
              )}
            </div>
          </div>

          <div className="top-action-buttons">
            <button
              className="btn btn-secondary"
              onClick={() => setShowDestModal(true)}
            >
              <Settings size={15} /> Set Drop Location
            </button>
            <button className="btn btn-primary" onClick={() => fileInputRef.current?.click()}>
              <Upload size={15} /> Upload Excel
            </button>
          </div>
        </div>

        {/* Drop Location Edit Modal */}
        {showDestModal && (
          <div className="modal-overlay" onClick={() => setShowDestModal(false)}>
            <div
              className="modal-container"
              style={{ maxWidth: 440 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="modal-header">
                <div className="modal-title-group">
                  <h3 className="modal-title">Set Drop Location</h3>
                </div>
                <button
                  type="button"
                  className="modal-close-btn"
                  onClick={() => setShowDestModal(false)}
                >
                  <X size={18} />
                </button>
              </div>
              <form onSubmit={handleSaveDestination}>
                <div className="modal-body">
                  <div style={{ marginBottom: 14 }}>
                    <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>
                      Location Name
                    </label>
                    <input
                      type="text"
                      className="search-box"
                      style={{ width: "100%" }}
                      value={destInputName}
                      onChange={(e) => setDestInputName(e.target.value)}
                      placeholder="e.g. Plant / Factory"
                    />
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                    <div>
                      <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>
                        Latitude
                      </label>
                      <input
                        type="text"
                        className="search-box"
                        style={{ width: "100%" }}
                        value={destInputLat}
                        onChange={(e) => setDestInputLat(e.target.value)}
                        placeholder="18.7766"
                      />
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: "0.78rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>
                        Longitude
                      </label>
                      <input
                        type="text"
                        className="search-box"
                        style={{ width: "100%" }}
                        value={destInputLon}
                        onChange={(e) => setDestInputLon(e.target.value)}
                        placeholder="73.6947"
                      />
                    </div>
                  </div>
                </div>
                <div className="modal-footer">
                  <button type="button" className="btn btn-secondary" onClick={() => setShowDestModal(false)}>
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-primary">
                    <Check size={16} /> Save Drop Location
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Pickup Locations Review & Verification Modal */}
        {showReviewModal && (
          <div className="modal-overlay" onClick={() => setShowReviewModal(false)}>
            <div
              className="modal-container"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Modal Header */}
              <div className="modal-header">
                <div className="modal-title-group">
                  <div className="modal-icon-badge">
                    <AlertTriangle size={18} />
                  </div>
                  <div>
                    <h3 className="modal-title">Pickup Locations Verification</h3>
                    <p className="modal-subtitle">
                      {unverifiedStopsSummary.length} unverified stops affecting {fileMeta?.unverifiedCount || 0} employees
                    </p>
                  </div>
                </div>
                <button
                  type="button"
                  className="modal-close-btn"
                  onClick={() => setShowReviewModal(false)}
                >
                  <X size={18} />
                </button>
              </div>

              {/* Action Box (Automated Corridor Resolution) */}
              <div className="modal-action-box">
                <div className="modal-action-info">
                  <div className="modal-action-title">
                    <Compass size={16} color="var(--primary)" />
                    <span>Automated Corridor Resolution</span>
                  </div>
                  <div className="modal-action-desc">
                    Verifies stops against PCMC/Talegaon industrial transit corridors and active employee clusters.
                  </div>
                </div>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={isAutoResolving || unverifiedStopsSummary.length === 0}
                  onClick={() => handleAgenticAutoResolve()}
                >
                  {isAutoResolving ? (
                    <>
                      <span className="spinner" /> Batch Resolving...
                    </>
                  ) : (
                    <>
                      <RefreshCw size={14} /> Batch Resolve Stops
                    </>
                  )}
                </button>
              </div>

              {/* Status Message */}
              {autoResolveStatus && (
                <div
                  style={{
                    padding: "8px 20px",
                    background: autoResolveStatus.includes("Error") ? "var(--danger-bg)" : "var(--success-bg)",
                    color: autoResolveStatus.includes("Error") ? "var(--danger)" : "var(--success)",
                    fontSize: "0.78rem",
                    fontWeight: 600,
                    borderBottom: "1px solid var(--border-subtle)",
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  {autoResolveStatus}
                </div>
              )}

              {/* Table of Unverified Stops */}
              <div className="modal-body">
                {unverifiedStopsSummary.length === 0 ? (
                  <div style={{ textAlign: "center", padding: "36px 20px" }}>
                    <CheckCircle2 size={36} color="var(--success)" style={{ marginBottom: 8 }} />
                    <div style={{ fontWeight: 700, color: "var(--text-primary)", fontSize: "1rem" }}>
                      All Pickup Locations Verified
                    </div>
                    <p style={{ color: "var(--text-muted)", fontSize: "0.82rem", marginTop: 4 }}>
                      All employees have verified coordinates and have been routed into the bus fleet.
                    </p>
                  </div>
                ) : (
                  <div className="table-scroll">
                    <table className="custom-table">
                      <thead>
                        <tr>
                          <th>Pickup Stop</th>
                          <th>Employees</th>
                          <th>Status / Reason</th>
                          <th style={{ width: 230 }}>Manual Override</th>
                        </tr>
                      </thead>
                      <tbody>
                        {unverifiedStopsSummary.map((item, idx) => {
                          const input = manualCoordsInput[item.stop_name] || {};
                          const candidates = expandedCandidates[item.stop_name];
                          const isLoading = loadingCandidates[item.stop_name];

                          return (
                            <React.Fragment key={idx}>
                              <tr>
                                <td>
                                  <strong style={{ color: "var(--text-primary)", display: "block" }}>
                                    {item.stop_name}
                                  </strong>
                                  <div style={{ display: "flex", gap: 6, marginTop: 4, flexWrap: "wrap" }}>
                                    <button
                                      type="button"
                                      className="btn btn-ghost btn-sm"
                                      disabled={isAutoResolving}
                                      onClick={() => handleAgenticAutoResolve([item.stop_name])}
                                    >
                                      <RefreshCw size={12} /> Auto-Match
                                    </button>
                                    <button
                                      type="button"
                                      className="btn btn-ghost btn-sm"
                                      disabled={isLoading}
                                      onClick={() => handleFetchCandidates(item.stop_name)}
                                    >
                                      <Search size={12} /> {isLoading ? "Searching..." : candidates ? "Hide Candidates" : "Inspect Map Candidates"}
                                    </button>
                                  </div>
                                </td>
                                <td>
                                  <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                                    {item.employees.length} pax
                                  </span>
                                  <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", marginTop: 2 }}>
                                    {item.employees.slice(0, 2).map((e) => e.employee_name).join(", ")}
                                    {item.employees.length > 2 && ` +${item.employees.length - 2} more`}
                                  </div>
                                </td>
                                <td>
                                  <span
                                    style={{
                                      display: "inline-block",
                                      padding: "2px 8px",
                                      borderRadius: "4px",
                                      background: "var(--warning-bg)",
                                      color: "var(--warning)",
                                      border: "1px solid var(--warning-border)",
                                      fontSize: "0.74rem",
                                      fontWeight: 600,
                                    }}
                                  >
                                    {item.reason}
                                  </span>
                                </td>
                                <td>
                                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                                    <input
                                      type="text"
                                      placeholder="Lat"
                                      value={input.lat || ""}
                                      onChange={(e) =>
                                        setManualCoordsInput((prev) => ({
                                          ...prev,
                                          [item.stop_name]: { ...prev[item.stop_name], lat: e.target.value },
                                        }))
                                      }
                                      style={{
                                        width: "68px",
                                        padding: "4px 6px",
                                        fontSize: "0.75rem",
                                        border: "1px solid var(--border-medium)",
                                        borderRadius: "4px",
                                      }}
                                    />
                                    <input
                                      type="text"
                                      placeholder="Lon"
                                      value={input.lon || ""}
                                      onChange={(e) =>
                                        setManualCoordsInput((prev) => ({
                                          ...prev,
                                          [item.stop_name]: { ...prev[item.stop_name], lon: e.target.value },
                                        }))
                                      }
                                      style={{
                                        width: "68px",
                                        padding: "4px 6px",
                                        fontSize: "0.75rem",
                                        border: "1px solid var(--border-medium)",
                                        borderRadius: "4px",
                                      }}
                                    />
                                    <button
                                      type="button"
                                      className="btn btn-secondary btn-sm"
                                      onClick={() => handleManualSaveCoordinate(item.stop_name)}
                                    >
                                      Save
                                    </button>
                                  </div>
                                </td>
                              </tr>
                              {candidates && (
                                <tr>
                                  <td colSpan={4} style={{ background: "var(--bg-subtle)", padding: "12px 14px", borderBottom: "2px solid var(--border-medium)" }}>
                                    <div style={{ fontSize: "0.76rem", fontWeight: 700, color: "var(--text-secondary)", marginBottom: 8, display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 4 }}>
                                      <span>Map Candidates (Ranked by Closeness to Employee Cluster):</span>
                                      <span style={{ color: "var(--text-muted)", fontWeight: 500 }}>Select the closest location to snap to bus corridor</span>
                                    </div>
                                    {candidates.length === 0 ? (
                                      <div style={{ fontSize: "0.75rem", color: "var(--text-dim)", fontStyle: "italic" }}>
                                        No candidates returned for this query.
                                      </div>
                                    ) : (
                                      <div className="candidate-list">
                                        {candidates.map((cand, cIdx) => {
                                          const distCluster = cand.min_dist_to_cluster_km;
                                          const isClose = distCluster !== null && distCluster <= 5.0;
                                          const isModerate = distCluster !== null && distCluster > 5.0 && distCluster <= 20.0;

                                          return (
                                            <div
                                              key={cIdx}
                                              className={`candidate-card ${isClose ? "candidate-best" : ""}`}
                                            >
                                              <div className="candidate-card-info">
                                                <div className="candidate-card-title">
                                                  {cand.formatted || "Unnamed location"}
                                                </div>
                                                <div className="candidate-card-meta">
                                                  <span>GPS: <b>{cand.lat}, {cand.lon}</b></span>
                                                  <span>To Plant: <b>~{cand.dist_to_plant_km} km</b></span>
                                                </div>
                                              </div>
                                              <div className="candidate-card-actions">
                                                {distCluster !== null && (
                                                  <span
                                                    className={`proximity-badge ${
                                                      isClose
                                                        ? "proximity-best"
                                                        : isModerate
                                                        ? "proximity-corridor"
                                                        : "proximity-outlier"
                                                    }`}
                                                  >
                                                    <span
                                                      className={`status-dot ${
                                                        isClose
                                                          ? "dot-success"
                                                          : isModerate
                                                          ? "dot-warning"
                                                          : "dot-danger"
                                                      }`}
                                                    />
                                                    {distCluster} km · {isClose ? "Best Match" : isModerate ? "Corridor Fit" : "Outlier"}
                                                  </span>
                                                )}
                                                <button
                                                  type="button"
                                                  className="btn btn-primary btn-sm"
                                                  onClick={() => handleSelectCandidate(item.stop_name, cand)}
                                                >
                                                  Select & Save
                                                </button>
                                              </div>
                                            </div>
                                          );
                                        })}
                                      </div>
                                    )}
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Modal Footer */}
              <div className="modal-footer">
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowReviewModal(false)}
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Results View */}
        {clustering ? (
          <div>
            {activeTab === "routes" && (
              <div className="kpi-row">
                <div className="kpi-card">
                  <span className="kpi-title">Total Employees</span>
                  <span className="kpi-number">{clustering.total_employees}</span>
                </div>
                <div className="kpi-card">
                  <span className="kpi-title">Buses Generated</span>
                  <span className="kpi-number">{clustering.total_buses}</span>
                </div>
                <div className="kpi-card">
                  <span className="kpi-title">Pickup Stops</span>
                  <span className="kpi-number">{clustering.total_stops}</span>
                </div>
              </div>
            )}

            {isOptimising && (
              <div className="routing-status" style={{ marginBottom: 12 }}>
                <span className="spinner" />
                Optimising on real road distances…
              </div>
            )}
            {roadRoutingError && (
              <div className="routing-status" role="alert" style={{ marginBottom: 12 }}>
                Road distance optimization is unavailable; routes are using straight-line
                estimates. {roadRoutingError}
              </div>
            )}

            {/* TAB 1: BUS ROUTES */}
            {activeTab === "routes" && (
              <div>
                <div className="filter-bar">
                  {/* Shift Filter */}
                  <div className="filter-group">
                    <span className="filter-label">Shift</span>
                    <div className="seg">
                      {["ALL", "A", "B", "C"].map((s) => (
                        <button
                          key={s}
                          className={`seg-btn ${shiftFilter === s ? "active" : ""}`}
                          onClick={() => setShiftFilter(s)}
                        >
                          {s === "ALL" ? "All" : s}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Corridor Filter */}
                  <div className="filter-group">
                    <span className="filter-label">Corridor</span>
                    <select
                      className="select-field"
                      value={corridorFilter}
                      onChange={(e) => setCorridorFilter(e.target.value)}
                    >
                      {["ALL", ...Array.from(new Set(clustering.routes.map((r) => r.corridor).filter(Boolean)))].map((c) => (
                        <option key={c} value={c}>
                          {c === "ALL" ? "All Corridors" : c}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="routes-grid">
                  {clustering.routes
                    .filter((r) => shiftFilter === "ALL" || r.shift.includes(shiftFilter))
                    .filter((r) => corridorFilter === "ALL" || r.corridor === corridorFilter)
                    .map((route) => (
                      <div
                        key={route.bus_id}
                        className="route-card"
                        onMouseEnter={() => setSelectedBusId(route.bus_id)}
                        onMouseLeave={() => setSelectedBusId(null)}
                      >
                        <div className="route-card-head">
                          <div>
                            <div className="route-card-title">{route.bus_id}</div>
                            <div className="route-card-shift">
                              {route.shift} • {route.corridor} Corridor
                            </div>
                            {(route.merged_from || route.split_from) && (
                              <div className="route-card-vehicle">
                                {route.merged_from && (
                                  <span className="route-card-flag">merged</span>
                                )}
                                {route.split_from && (
                                  <span className="route-card-flag">split</span>
                                )}
                              </div>
                            )}
                          </div>
                          <span className="badge-seats">
                            {route.passengers_assigned} / 32 seats
                          </span>
                        </div>
                        {route.ride_time_status === "EXCEEDS_LIMIT" && (
                          <div className="route-time-warning" role="status">
                            Longest ride exceeds the 70-minute limit; review this route.
                          </div>
                        )}

                        <div className="route-card-body">
                          <div className="route-stats-bar">
                            <div className="stat-group">
                              <span className="stat-label">Stops</span>
                              <span className="stat-value">{route.total_stops}</span>
                            </div>
                            <div className="stat-group">
                              <span className="stat-label">Longest ride</span>
                              <span
                                className={`stat-value ${
                                  route.ride_time_status === "EXCEEDS_LIMIT"
                                    ? "ride-time-exceeded"
                                    : ""
                                }`}
                              >
                                ~{Math.round(route.longest_ride_minutes)} min
                              </span>
                            </div>
                          </div>

                          <button
                            className="route-card-pdf"
                            onClick={() => handleRoutePdf(route)}
                            title="Download this route as a driver route sheet (PDF)"
                          >
                            <FileText size={13} />
                            Download route sheet
                          </button>

                          <div className="stop-list-scroll">
                            {route.stops.map((st) => (
                              <div
                                key={st.seq}
                                className="stop-item"
                                style={{ cursor: "pointer" }}
                                onClick={() => {
                                  setActiveTab("map");
                                  setSelectedBusId(route.bus_id);
                                }}
                              >
                                <span className="stop-idx">{st.seq}</span>
                                <span className="stop-label">{st.stop_name}</span>
                                <span className="stop-distance">
                                  {st.distance_from_prev_km > 0
                                    ? `+${st.distance_from_prev_km} km`
                                    : "Start"}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    ))}
                </div>
              </div>
            )}

            {/* Employees */}
            {activeTab === "employees" && (
              <div className="table-panel">
                <div className="table-filter-bar">
                  <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                    <div className="table-summary">
                      <strong>{filteredEmployees.length}</strong> of{" "}
                      <strong>{clustering.employees.length}</strong> employees
                    </div>
                    <div className="seg">
                      <button
                        className={`seg-btn ${employeeStatusFilter === "ALL" ? "active" : ""}`}
                        onClick={() => setEmployeeStatusFilter("ALL")}
                      >
                        All ({clustering.employees.length})
                      </button>
                      {fileMeta?.unverifiedCount > 0 && (
                        <button
                          className={`seg-btn ${employeeStatusFilter === "UNVERIFIED" ? "active" : ""}`}
                          onClick={() => setEmployeeStatusFilter("UNVERIFIED")}
                          style={
                            employeeStatusFilter === "UNVERIFIED"
                              ? { background: "#FEF3C7", color: "#92400E", borderColor: "#F59E0B", fontWeight: 700 }
                              : { color: "#B45309", fontWeight: 600 }
                          }
                        >
                          ⚠️ Needs Review ({fileMeta.unverifiedCount})
                        </button>
                      )}
                    </div>
                  </div>
                  <input
                    type="text"
                    className="search-box"
                    placeholder="Search employee, stop, or bus..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>

                <div className="table-scroll">
                  <table className="custom-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>ID</th>
                        <th>Employee Name</th>
                        <th>Shift</th>
                        <th>Assigned Bus</th>
                        <th>Pickup Point</th>
                        <th>Mobile</th>
                        <th>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredEmployees.map((e) => (
                        <tr
                          key={e.sr_no}
                          onMouseEnter={() => setHoveredEmployee(e)}
                          onMouseLeave={() => setHoveredEmployee(null)}
                          style={{
                            background:
                              hoveredEmployee && hoveredEmployee.sr_no === e.sr_no
                                ? "var(--warning-bg)"
                                : hoveredEmployee && hoveredEmployee.assigned_bus === e.assigned_bus
                                ? "var(--accent-light)"
                                : "transparent",
                            transition: "background 0.12s ease",
                          }}
                        >
                          <td style={{ color: "var(--text-dim)", fontFamily: "monospace" }}>
                            {e.sr_no}
                          </td>
                          <td style={{ fontFamily: "monospace" }}>{e.employee_id || "-"}</td>
                          <td title={e.employee_name} style={{ fontWeight: 600 }}>
                            {e.employee_name}
                            {hoveredEmployee && hoveredEmployee.sr_no === e.sr_no && (
                              <span style={{ fontSize: "0.7rem", color: "var(--warning)", marginLeft: 6, fontWeight: 700 }}>
                                (Inspecting Route)
                              </span>
                            )}
                          </td>
                          <td>
                            <span className="badge-seats">{e.shift}</span>
                          </td>
                          <td>
                            <span
                              className="bus-badge"
                              title={e.resolution_reason || e.location_status || ""}
                              style={e.assigned_bus === "Unassigned" ? { color: "var(--warning)" } : undefined}
                            >
                              {e.assigned_bus}
                            </span>
                          </td>
                          <td>{e.pickup_point}</td>
                          <td
                            title={e.mobile || ""}
                            style={{ fontFamily: "monospace", color: "var(--text-muted)" }}
                          >
                            {e.mobile || "-"}
                          </td>
                          <td>
                            {e.location_status === "LOCATION_UNVERIFIED" || e.assigned_bus === "Unassigned" ? (
                              <button
                                className="btn btn-secondary btn-sm"
                                style={{
                                  color: "var(--warning)",
                                  background: "var(--warning-bg)",
                                  borderColor: "var(--warning-border)",
                                  fontWeight: 600,
                                }}
                                onClick={() => setShowReviewModal(true)}
                              >
                                <AlertTriangle size={12} /> Fix Location
                              </button>
                            ) : (
                              <button
                                className="btn btn-secondary btn-sm"
                                onClick={() => {
                                  setHoveredEmployee(e);
                                  setActiveTab("map");
                                }}
                                disabled={!Number.isFinite(e.lat) || !Number.isFinite(e.lon)}
                              >
                                <Crosshair size={12} /> View Map
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* TAB 3: ROUTE MAP */}
            {activeTab === "map" && (
              <div>
                {hoveredEmployee && (
                  <div
                    style={{
                      background: "var(--accent-light)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "var(--radius-sm)",
                      padding: "10px 14px",
                      marginBottom: "14px",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: 8,
                    }}
                  >
                    <div>
                      <span style={{ fontWeight: 700, color: "var(--accent)" }}>
                        Inspecting Employee: {hoveredEmployee.employee_name}
                      </span>
                      <span style={{ color: "var(--text-secondary)", marginLeft: 10, fontSize: "0.82rem" }}>
                        Pickup: <b>{hoveredEmployee.pickup_point}</b> &rarr; Assigned to <b>{hoveredEmployee.assigned_bus}</b> ({hoveredEmployee.shift})
                      </span>
                    </div>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => setHoveredEmployee(null)}
                    >
                      Clear Selection
                    </button>
                  </div>
                )}

                <RouteMap
                  routes={clustering.routes}
                  destination={destination}
                  activeBusId={selectedBusId}
                  hoveredEmployee={hoveredEmployee}
                  onSelectBus={(busId) => setSelectedBusId(busId)}
                />
              </div>
            )}
          </div>
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              padding: "80px 20px",
            }}
          >
            <h3>No Employee Data Loaded</h3>
            <p style={{ marginTop: 8, color: "var(--text-muted)" }}>
              Upload an Excel file to generate bus routes.
            </p>
            <div style={{ display: "flex", gap: 10, marginTop: 18 }}>
              <button className="btn btn-primary" onClick={() => fileInputRef.current?.click()}>
                <Upload size={16} /> Upload Excel
              </button>
              <button className="btn btn-secondary" onClick={handleLoadSample} disabled={isUploading}>
                Load Sample Data
              </button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
