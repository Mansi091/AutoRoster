import React, { useState, useMemo, useRef, useEffect } from "react";
import { useAuth, useUser } from "@clerk/react";
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
  Search,
  History,
  LogIn,
  LogOut,
  Shield,
  Clock3,
  ExternalLink,
} from "lucide-react";
import { exportToExcel } from "./utils/excel";
import { computeFleetCost } from "./utils/fleetCost";
import { exportRoutePdf } from "./utils/routePdf";
import RouteMap from "./components/RouteMap";
import AuthModal from "./components/AuthModal";
import HistoryPanel from "./components/HistoryPanel";
import { recordRosterHistory, authenticatedFetch } from "./utils/clerk";

function adaptMultiShiftResponse(shifts, destination) {
  const routes = [];
  const employees = [];
  let totalBuses = 0;
  let totalStops = 0;
  let totalFleetKm = 0;

  (shifts || []).forEach((shiftData) => {
    const shiftName = shiftData.shift || "A";
    (shiftData.routes || []).forEach((r) => {
      totalBuses += 1;
      const busId = `Shift ${shiftName} - Route ${r.route_no}`;
      totalFleetKm += r.total_distance_km || 0;
      totalStops += (r.stops || []).length;

      const adaptedStops = (r.stops || []).map((s, idx) => ({
        seq: idx + 1,
        stop_name: s.stop_name,
        lat: s.lat,
        lon: s.lon,
        demand: s.demand,
        pickup_time: s.scheduled_time,
        distance_from_prev_km: idx === 0 ? 0 : Number(r.leg_distances_km?.[idx - 1]) || null,
        employees: s.employees || [],
      }));

      // Gather employees
      (r.stops || []).forEach((s) => {
        (s.employees || []).forEach((emp) => {
          employees.push({
            employee_id: emp.sr_no || employees.length + 1,
            employee_name: emp.emp_name,
            grade: emp.emp_type || "DET",
            shift: shiftName,
            pickup_point: s.stop_name,
            mobile: emp.mobile && emp.mobile !== "nan" ? emp.mobile : "-",
            latitude: s.lat,
            longitude: s.lon,
            assigned_bus: busId,
            location_status: "VERIFIED",
          });
        });
      });

      routes.push({
        bus_id: busId,
        route_no: r.route_no,
        route_id: r.route_id || busId,
        shift: shiftName,
        corridor: r.corridor_name || "Transit Corridor",
        passengers_assigned: r.total_passengers,
        total_stops: adaptedStops.length,
        total_km: r.total_distance_km,
        longest_ride_minutes: r.route_duration_min,
        duration_source: r.duration_source,
        first_pickup_time: r.first_pickup_time,
        plant_arrival_time: r.plant_arrival_time,
        road_geometry: r.road_geometry || null,
        ride_time_status: r.ride_time_status || null,
        stops: adaptedStops,
      });
    });

    // Outliers
    (shiftData.outliers || []).forEach((outlier) => {
      employees.push({
        employee_id: outlier.sr_no || employees.length + 1,
        employee_name: outlier.emp_name,
        grade: outlier.emp_type || "Staff",
        shift: shiftName,
        pickup_point: outlier.raw_stop,
        mobile: outlier.mobile && outlier.mobile !== "nan" ? outlier.mobile : "-",
        latitude: null,
        longitude: null,
        assigned_bus: "Needs Attention (Outlier)",
        location_status: "LOCATION_UNVERIFIED",
        resolution_reason: outlier.reason || "Commute time exceeds 70m ceiling",
      });
    });
  });

  return {
    routes,
    employees,
    routed_employees: employees.filter((e) => e.location_status === "VERIFIED").length,
    unverified_employees: employees.filter((e) => e.location_status !== "VERIFIED").length,
    total_employees: employees.length,
    total_buses: totalBuses,
    total_stops: totalStops,
    total_fleet_km: Number(totalFleetKm.toFixed(1)),
    destination,
  };
}

export default function App() {
  const { isLoaded, isSignedIn, userId, getToken, signOut } = useAuth();
  const { user } = useUser();
  const currentUser = isSignedIn && user ? {
    id: userId,
    email: user.primaryEmailAddress?.emailAddress || "",
    display_name: user.fullName || user.firstName || "Supervisor",
    role: "supervisor",
  } : null;
  const [activeTab, setActiveTab] = useState("map"); // "routes" | "employees" | "map"
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [shiftFilter, setShiftFilter] = useState("ALL");
  const [corridorFilter, setCorridorFilter] = useState("ALL");
  const [searchQuery, setSearchQuery] = useState("");
  const [shiftStartTimes, setShiftStartTimes] = useState({});
  const [selectedTimeShift, setSelectedTimeShift] = useState("");
  const [plannedRosterFile, setPlannedRosterFile] = useState(null);
  const [plannedShiftStartTimes, setPlannedShiftStartTimes] = useState(null);
  const [arrivalBufferMinutes, setArrivalBufferMinutes] = useState(null);
  const fileInputRef = useRef(null);
  const [isRosterDragOver, setIsRosterDragOver] = useState(false);

  // Drop-off Destination & Confirmation State (No hardcoded drop-off; must be confirmed by supervisor)
  const [destination, setDestination] = useState(null);
  const [confirmedDropoff, setConfirmedDropoff] = useState(null);
  const [dropoffSearchText, setDropoffSearchText] = useState("");
  const [dropoffCandidates, setDropoffCandidates] = useState([]);
  const [isSearchingDropoff, setIsSearchingDropoff] = useState(false);
  const [dropoffSearchError, setDropoffSearchError] = useState("");
  const [showDropoffModal, setShowDropoffModal] = useState(false);
  const [isConfirmingDropoff, setIsConfirmingDropoff] = useState(false);

  // Optimization & Routing State
  const [clustering, setClustering] = useState(null);
  const [uploadedRosterFile, setUploadedRosterFile] = useState(null);
  const [isOptimising, setIsOptimising] = useState(false);
  const isPlanning = isOptimising && Boolean(uploadedRosterFile);
  const [statusMessage, setStatusMessage] = useState("");
  const [hoveredEmployee, setHoveredEmployee] = useState(null);
  const [selectedBusId, setSelectedBusId] = useState(null);
  const [employeeStatusFilter, setEmployeeStatusFilter] = useState("ALL");

  const activeUserIdRef = useRef(null);
  const optimizationAbortRef = useRef(null);
  const isSwitchingAccount = Boolean(activeUserIdRef.current && userId && activeUserIdRef.current !== userId);

  // Keep in-memory route data isolated if the active Clerk account changes.
  useEffect(() => {
    if (activeUserIdRef.current && activeUserIdRef.current !== userId) {
      optimizationAbortRef.current?.abort();
      setClustering(null);
      setUploadedRosterFile(null);
      setPlannedRosterFile(null);
      setSelectedTimeShift("");
      setDestination(null);
      setConfirmedDropoff(null);
      setActiveTab("map");
      setStatusMessage("");
    }
    activeUserIdRef.current = userId || null;
  }, [userId]);

  // A previous supervisor selection must never silently start a new roster run.
  // Require an explicit facility confirmation in the current app session.
  useEffect(() => {
    setDestination(null);
    setConfirmedDropoff(null);
  }, [currentUser?.id]);

  useEffect(() => {
    if (!isLoaded || !isSignedIn) {
      setShiftStartTimes({});
      setSelectedTimeShift("");
      setPlannedShiftStartTimes(null);
      setArrivalBufferMinutes(null);
      return undefined;
    }
    let cancelled = false;
    const loadPlanningOptions = async () => {
      try {
        const response = await authenticatedFetch("/api/planning-options", {}, getToken);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || payload.error || "Could not load shift times.");
        const startTimes = payload.shift_start_times || {};
        if (!cancelled) {
          setShiftStartTimes(startTimes);
          setArrivalBufferMinutes(Number(payload.arrival_buffer_minutes) || 0);
        }
      } catch (error) {
        if (!cancelled) setStatusMessage(`Error: ${error.message}`);
      }
    };
    loadPlanningOptions();
    return () => { cancelled = true; };
  }, [isLoaded, isSignedIn, getToken]);

  const openDropoffPicker = () => {
    // Changing the destination clears the old selection immediately. If the
    // picker is closed without confirming a replacement, planning stays blocked.
    setDestination(null);
    setConfirmedDropoff(null);
    setSelectedTimeShift("");
    setShowDropoffModal(true);
  };

  const handleSignOut = async () => {
    try {
      optimizationAbortRef.current?.abort();
      activeUserIdRef.current = null;
      await signOut();
      setClustering(null);
      setUploadedRosterFile(null);
      setPlannedRosterFile(null);
      setActiveTab("map");
      setStatusMessage("");
    } catch (err) {
      setStatusMessage(`Error: Could not sign out. ${err.message}`);
    }
  };

  const runOptimization = async (file = uploadedRosterFile, facility = destination) => {
    if (!file) {
      setStatusMessage("Choose an Excel roster before planning routes.");
      return;
    }
    if (!facility) {
      setUploadedRosterFile(file);
      setStatusMessage("Choose and confirm a drop-off facility before planning routes.");
      setShowDropoffModal(true);
      return;
    }
    if (!Object.keys(shiftStartTimes).length) {
      setStatusMessage("Error: Shift options are unavailable. Please reload and try again.");
      return;
    }
    if (!selectedTimeShift) {
      setStatusMessage("Error: Select a shift before building routes.");
      return;
    }
    if (Object.values(shiftStartTimes).some((time) => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time || ""))) {
      setStatusMessage("Error: Enter a valid start time for every shift before planning.");
      return;
    }
    const planningUserId = currentUser?.id;
    if (!planningUserId) return;
    optimizationAbortRef.current?.abort();
    const abortController = new AbortController();
    optimizationAbortRef.current = abortController;
    setUploadedRosterFile(file);
    setIsOptimising(true);
    setStatusMessage("");
    try {
      const planningAccessToken = await getToken();
      if (!planningAccessToken) throw new Error("Your sign-in has expired. Please sign in again.");
      if (activeUserIdRef.current !== planningUserId || abortController.signal.aborted) return;
      const formData = new FormData();
      formData.append("file", file);
      formData.append("dropoff", JSON.stringify({
        name: facility.name,
        lat: facility.lat,
        lon: facility.lon,
      }));
      formData.append("shift_start_times", JSON.stringify(shiftStartTimes));
      const res = await authenticatedFetch("/api/plan_multi_shift", {
        method: "POST",
        body: formData,
        signal: abortController.signal,
        }, planningAccessToken);

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || data.detail || `Optimizer returned HTTP ${res.status}`);
      if (abortController.signal.aborted || activeUserIdRef.current !== planningUserId) return;
      if (data.status === "SUCCESS" && data.shifts) {
        const adapted = adaptMultiShiftResponse(data.shifts, facility);
        setClustering(adapted);
        setPlannedRosterFile(file);
        setPlannedShiftStartTimes({ ...shiftStartTimes });
        setStatusMessage("");

        // Save this run to the signed-in supervisor's private history.
        try {
          await recordRosterHistory({
            uploaded_file_name: file.name,
            total_employees: adapted.total_employees,
            routed_employees: adapted.routed_employees,
            total_routes: adapted.routes.length,
            destination_name: facility.name || "",
            shifts_summary: data.shifts || [],
            created_at: new Date().toISOString(),
          }, planningAccessToken);
        } catch (historyError) {
          console.error("Could not archive roster history in Supabase:", historyError);
          setStatusMessage(`Routes are ready, but this run could not be saved to roster history: ${historyError.message}`);
        }
      } else {
        throw new Error(data.error || "Optimization failed");
      }
    } catch (err) {
      if (err.name === "AbortError") return;
      console.error("Optimization failed:", err);
      setStatusMessage(`Error: ${err.message}`);
    } finally {
      if (optimizationAbortRef.current === abortController) setIsOptimising(false);
    }
  };

  // Drop-off Location Search (LocationIQ Autocomplete)
  useEffect(() => {
    const controller = new AbortController();
    if (!dropoffSearchText || dropoffSearchText.trim().length < 2) {
      setDropoffCandidates([]);
      setDropoffSearchError("");
      setIsSearchingDropoff(false);
      return;
    }

    setDropoffSearchError("");
    const timer = setTimeout(async () => {
      setIsSearchingDropoff(true);
      try {
        const res = await authenticatedFetch(
          `/api/dropoff/search?q=${encodeURIComponent(dropoffSearchText.trim())}`,
          { signal: controller.signal },
          getToken,
        );
        const payload = await res.json();
        if (!res.ok) throw new Error(payload.detail || "Location search is temporarily unavailable.");
        setDropoffCandidates(Array.isArray(payload) ? payload : []);
      } catch (err) {
        if (err.name !== "AbortError") {
          console.warn("Drop-off search error:", err);
          setDropoffCandidates([]);
          setDropoffSearchError(err.message || "Location search is temporarily unavailable.");
        }
      } finally {
        if (!controller.signal.aborted) setIsSearchingDropoff(false);
      }
    }, 350);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [dropoffSearchText, getToken]);

  // Supervisor Drop-off Confirmation
  const handleConfirmDropoff = async (item) => {
    setIsConfirmingDropoff(true);
    try {
      const payload = {
        name: item.name,
        address: item.address,
        lat: item.lat,
        lon: item.lon,
        confirmed_by: "Supervisor (Web Dashboard)",
      };
      const res = await authenticatedFetch("/api/dropoff/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }, getToken);
      if (res.ok) {
        const data = await res.json();
        setConfirmedDropoff(data.dropoff);
        setClustering(null);
        setPlannedRosterFile(null);
        setDestination({
          name: data.dropoff.name,
          lat: data.dropoff.lat,
          lon: data.dropoff.lon,
        });
        setDropoffCandidates([]);
        setDropoffSearchText("");
        setShowDropoffModal(false);
      }
    } catch (err) {
      alert(`Could not confirm drop-off: ${err.message}`);
    } finally {
      setIsConfirmingDropoff(false);
    }
  };

  // Direct Excel Download (Finalized Images 2 & 3 Format)
  const handleDownloadFinalExcel = async () => {
    try {
      const res = await authenticatedFetch("/api/download_final_roster", {}, getToken);
      if (!res.ok) throw new Error("No completed roster is available for this account yet.");
      const url = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "FINAL_OPTIMIZED_BUS_ROSTER.xlsx";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setStatusMessage(`Error: ${err.message}`);
    }
  };

  const handleFileUpload = (e) => {
    const file = e.target.files?.[0];
    if (file) {
      if (!/\.xlsx?$/i.test(file.name)) {
        setStatusMessage("Error: Choose an .xlsx or .xls file.");
        e.target.value = "";
        return;
      }
      setUploadedRosterFile(file);
      setSelectedTimeShift("");
      setStatusMessage("");
    }
    e.target.value = "";
  };

  const fleetCost = useMemo(() => {
    if (!clustering) return null;
    return computeFleetCost(clustering.routes, {
      totalEmployees: clustering.routed_employees,
      capacity: 32,
    });
  }, [clustering]);

  const longestRoute = clustering?.routes?.reduce((currentLongest, route) => {
    if (!currentLongest) return route;
    return Number(route.longest_ride_minutes) > Number(currentLongest.longest_ride_minutes)
      ? route
      : currentLongest;
  }, null) || null;
  const routesNeedingReview = clustering?.routes?.filter(
    (route) => Number.isFinite(Number(route.longest_ride_minutes)) && Number(route.longest_ride_minutes) > 70
  ) || [];
  const assignmentRate = clustering?.total_employees > 0
    ? Math.min(100, Math.round((clustering.routed_employees / clustering.total_employees) * 100))
    : 0;
  const hasPendingRoster = Boolean(uploadedRosterFile && uploadedRosterFile !== plannedRosterFile);
  const canBuildRoutes = Boolean(
    hasPendingRoster && destination && selectedTimeShift &&
    Object.keys(shiftStartTimes).length > 0 &&
    Object.values(shiftStartTimes).every((time) => /^([01]\d|2[0-3]):[0-5]\d$/.test(time || "")) &&
    !isOptimising
  );

  const handleRoutePdf = (route) => {
    const busCost = fleetCost?.per_bus?.find((b) => b.bus_id === route.bus_id);
    exportRoutePdf(route, {
      destination,
      roadStats: busCost
        ? {
            distanceKm: busCost.one_way_km,
            durationMinutes: Math.round(route.longest_ride_minutes || 0),
          }
        : null,
    });
  };

  // Filtered employees for table
  const filteredEmployees = useMemo(() => {
    if (!clustering) return [];
    let list = clustering.employees;
    if (shiftFilter !== "ALL") {
      list = list.filter((e) => e.shift && e.shift.toUpperCase().includes(shiftFilter));
    }
    if (employeeStatusFilter === "UNVERIFIED") {
      list = list.filter((e) => e.location_status === "LOCATION_UNVERIFIED" || e.assigned_bus.includes("Outlier"));
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

  const needsShiftTimeRerun = Boolean(
    plannedShiftStartTimes && Object.keys(shiftStartTimes).some((shift) => plannedShiftStartTimes[shift] !== shiftStartTimes[shift])
  );

  if (!isLoaded || isSwitchingAccount) {
    return <div className="auth-loading-screen"><span className="spinner" /> Checking supervisor session...</div>;
  }

  if (!isSignedIn) return <AuthModal />;

  return (
    <div className="app-layout">
      {/* Hidden file input */}
      <input
        type="file"
        ref={fileInputRef}
        accept=".xlsx, .xls"
        style={{ display: "none" }}
        onChange={handleFileUpload}
      />

      {/* Mobile Top Navigation */}
      <header className="mobile-header">
        <div className="mobile-brand">
          <Bus size={20} color="var(--primary)" />
          <span>AutoRoster Transit</span>
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

      {/* Sidebar Navigation */}
      <aside className={`sidebar ${mobileMenuOpen ? "open" : ""}`}>
        <div className="sidebar-brand">
          <h1 className="brand-title-simple">
            <span className="brand-mark"><Bus size={20} /></span>
            <span className="brand-name">AutoRoster</span>
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
            className={`sidebar-tab-btn ${clustering && activeTab === "map" ? "active" : ""}`}
            disabled={!clustering}
            title={!clustering ? "Upload a roster to view routes" : "Interactive map"}
            onClick={() => {
              setActiveTab("map");
              setMobileMenuOpen(false);
            }}
          >
            <MapPin size={18} />
            <span>Interactive Map</span>
          </button>
          <button
            className={`sidebar-tab-btn ${clustering && activeTab === "routes" ? "active" : ""}`}
            disabled={!clustering}
            title={!clustering ? "Upload a roster to view routes" : "Optimized routes"}
            onClick={() => {
              setActiveTab("routes");
              setMobileMenuOpen(false);
            }}
          >
            <Bus size={18} />
            <span>Optimized Routes</span>
            {clustering && <span className="tab-badge">{clustering.routes.length}</span>}
          </button>
          <button
            className={`sidebar-tab-btn ${clustering && activeTab === "employees" ? "active" : ""}`}
            disabled={!clustering}
            title={!clustering ? "Upload a roster to view employees" : "Employees"}
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
            className={`sidebar-tab-btn ${activeTab === "history" ? "active" : ""}`}
            onClick={() => {
              setActiveTab("history");
              setMobileMenuOpen(false);
            }}
          >
            <History size={18} />
            <span>Roster History</span>
          </button>
        </nav>

        {/* Sidebar Actions & Download */}
        <div className="sidebar-footer">
          {/* Supervisor Auth Status */}
          <div className="sidebar-auth-panel">
            {currentUser && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span style={{ fontSize: "0.75rem", fontWeight: 700, color: "#16A34A", display: "flex", alignItems: "center", gap: 4 }}>
                    <Shield size={12} /> SUPERVISOR
                  </span>
                  <button
                    type="button"
                    onClick={handleSignOut}
                    style={{
                      background: "none",
                      border: "none",
                      color: "#DC2626",
                      fontSize: "0.72rem",
                      fontWeight: 700,
                      cursor: "pointer",
                      display: "flex",
                      alignItems: "center",
                      gap: 3,
                    }}
                  >
                    <LogOut size={12} /> Sign Out
                  </button>
                </div>
                <div style={{ fontSize: "0.8rem", color: "#334155", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={currentUser.email}>
                  {currentUser.email}
                </div>
              </div>
            )}
          </div>

          {clustering && <>
          <button
            className="btn btn-secondary"
            style={{ width: "100%", justifyContent: "center" }}
            onClick={() => runOptimization()}
            disabled={isOptimising}
          >
            <RefreshCw size={15} className={isOptimising ? "spin" : ""} />
            <span>{isOptimising ? "Optimizing…" : "Re-Run Optimization"}</span>
          </button>

          <button
            className="btn btn-primary sidebar-download-btn"
            onClick={handleDownloadFinalExcel}
            title="Download finalized multi-shift roster formatted strictly to Image 2 & 3"
          >
            <Download size={16} />
            <span>Download Final Excel</span>
          </button>
          </>}
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="main-content">
        {/* Clean Drop-off Destination Bar */}
        <div className="facility-toolbar">
          <div className="facility-toolbar-main" style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            {destination ? (
              <>
                <MapPin size={18} color="#2563EB" />
                <span style={{ fontSize: "0.88rem", fontWeight: 700, color: "#1E293B" }}>
                  Drop-off Facility:
                </span>
                <span style={{ fontSize: "0.88rem", color: "#475569" }}>
                  {`${destination.name} (${destination.lat.toFixed(4)}, ${destination.lon.toFixed(4)})`}
                </span>
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={openDropoffPicker}
                  style={{ fontSize: "0.78rem", padding: "4px 10px", marginLeft: 4 }}
                >
                  <Search size={13} /> Change Drop-off
                </button>
              </>
            ) : (
              <button
                className="btn btn-secondary btn-sm"
                onClick={openDropoffPicker}
                style={{ fontSize: "0.82rem", padding: "7px 12px" }}
              >
                <MapPin size={15} color="#2563EB" /> Choose drop-off facility
              </button>
            )}
            {destination && Object.keys(shiftStartTimes).length > 0 && (
              <div className="shift-deadline-controls">
                <label className="shift-deadline-field">
                  <span>Shift</span>
                  <select
                    value={selectedTimeShift}
                    onChange={(event) => {
                      setSelectedTimeShift(event.target.value);
                      setStatusMessage("");
                    }}
                    disabled={isOptimising}
                    aria-label="Select shift to set its start time"
                  >
                    <option value="">Select shift</option>
                    {Object.keys(shiftStartTimes).sort().map((shift) => (
                      <option key={shift} value={shift}>Shift {shift}</option>
                    ))}
                  </select>
                </label>
                <label className="shift-deadline-field">
                  <span>Shift starts at</span>
                  <input
                    type="time"
                    required
                    value={shiftStartTimes[selectedTimeShift] || ""}
                    onChange={(event) => {
                      setShiftStartTimes((current) => ({
                        ...current,
                        [selectedTimeShift]: event.target.value,
                      }));
                      setStatusMessage("");
                    }}
                    disabled={isOptimising || !selectedTimeShift}
                    aria-label={`Start time for Shift ${selectedTimeShift}`}
                  />
                </label>
                {needsShiftTimeRerun && clustering && (
                  <span className="shift-deadline-pending">Re-run to apply</span>
                )}
                <span className="shift-deadline-note">
                  Routes arrive {arrivalBufferMinutes ?? 0} min before the selected shift starts.
                </span>
              </div>
            )}
          </div>

          <div className="facility-toolbar-actions" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {clustering && (
              <button
                className="btn btn-secondary"
                onClick={() => fileInputRef.current?.click()}
                style={{ display: "flex", alignItems: "center", gap: 6 }}
              >
                <Upload size={15} /> Upload New Roster
              </button>
            )}
          </div>
        </div>

        {statusMessage.startsWith("Error:") && !isPlanning && (
          <div className="route-error-banner" role="alert">{statusMessage}</div>
        )}

        {!clustering && !isPlanning && activeTab !== "history" && (
          <section className="roster-upload-start">
            <div className="roster-upload-heading">
              <span className="roster-upload-eyebrow"><span /> ROUTE PLANNING</span>
              <h2>Plan employee transport</h2>
              <p>Upload an employee roster to begin route planning.</p>
            </div>
            <div className="roster-upload-grid">
              <div
                className={`roster-dropzone ${isRosterDragOver ? "is-dragging" : ""}`}
                onDragOver={(event) => { event.preventDefault(); setIsRosterDragOver(true); }}
                onDragLeave={(event) => {
                  if (!event.currentTarget.contains(event.relatedTarget)) setIsRosterDragOver(false);
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  setIsRosterDragOver(false);
                  const file = event.dataTransfer.files?.[0];
                  if (file) {
                    if (!/\.xlsx?$/i.test(file.name)) {
                      setStatusMessage("Error: Choose an .xlsx or .xls file.");
                    } else {
                      setUploadedRosterFile(file);
                      setSelectedTimeShift("");
                      setStatusMessage("");
                    }
                  }
                }}
                onClick={() => fileInputRef.current?.click()}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    fileInputRef.current?.click();
                  }
                }}
              >
                <div className="roster-upload-icon"><FileText size={24} /></div>
                <h3>Drop your Excel roster here</h3>
                <p>or <span>browse files</span> on your computer</p>
                <div className="roster-dropzone-meta">
                  <span><CheckCircle2 size={14} /> Excel workbook</span>
                  <span>.XLSX or .XLS</span>
                </div>
              </div>
            </div>
          </section>
        )}

        {hasPendingRoster && !isPlanning && activeTab !== "history" && (
          <section className="roster-upload-ready" aria-label="Build routes from selected roster">
            <div className="roster-upload-ready-file">
              <span className="roster-upload-ready-icon"><FileText size={18} /></span>
              <div>
                <strong title={uploadedRosterFile.name}>{uploadedRosterFile.name}</strong>
                <span>
                  {!destination
                    ? "Choose a drop-off facility to continue."
                    : !Object.keys(shiftStartTimes).length
                      ? "Loading shift options…"
                      : !selectedTimeShift
                        ? "Select a shift to review its start time."
                        : !Object.values(shiftStartTimes).every((time) => /^([01]\d|2[0-3]):[0-5]\d$/.test(time || ""))
                          ? "Enter a valid start time for every shift."
                          : "Roster ready to plan."}
                </span>
              </div>
            </div>
            <button
              type="button"
              className="btn btn-primary roster-build-button"
              onClick={() => runOptimization()}
              disabled={!canBuildRoutes}
            >
              <Bus size={16} /> Build routes
            </button>
          </section>
        )}

        {/* Drop-off location search */}
        {showDropoffModal && (
          <div className="modal-overlay" onClick={() => setShowDropoffModal(false)}>
            <div className="modal-container dropoff-search-modal" onClick={(event) => event.stopPropagation()}>
              <div className="modal-header">
                <div className="modal-title-group">
                  <h3 className="modal-title"><MapPin size={19} color="#536b82" /> Choose drop-off facility</h3>
                </div>
                <button type="button" className="modal-close-btn" onClick={() => setShowDropoffModal(false)} aria-label="Close search">
                  <X size={18} />
                </button>
              </div>

              <div className="modal-body dropoff-search-body">
                <div className="dropoff-search-field">
                  <Search size={17} />
                  <input
                    type="text"
                    placeholder="Search a place or paste a Google Maps link"
                    value={dropoffSearchText}
                    onChange={(event) => {
                      setDropoffSearchText(event.target.value);
                      setDropoffCandidates([]);
                    }}
                    autoFocus
                    aria-label="Search for a drop-off facility"
                  />
                  {isSearchingDropoff && <span className="spinner" />}
                </div>

                <div className="dropoff-search-results">
                  {dropoffSearchError ? (
                    <div className="dropoff-search-message is-error" role="alert">{dropoffSearchError}</div>
                  ) : dropoffCandidates.length > 0 ? (
                    dropoffCandidates.map((place, index) => (
                      <article className="dropoff-result-row" key={`${place.lat}-${place.lon}-${index}`}>
                        <div className="dropoff-result-copy">
                          <strong>{place.name}</strong>
                          <span>{place.address}</span>
                          <small>{Number(place.lat).toFixed(4)}, {Number(place.lon).toFixed(4)} · {place.source}</small>
                        </div>
                        <div className="dropoff-result-actions">
                          <a
                            className="dropoff-map-link"
                            href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${place.lat},${place.lon}`)}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <ExternalLink size={14} /> Map
                          </a>
                          <button type="button" className="btn btn-primary btn-sm" disabled={isConfirmingDropoff} onClick={() => handleConfirmDropoff(place)}>
                            <Check size={14} /> Confirm
                          </button>
                        </div>
                      </article>
                    ))
                  ) : isSearchingDropoff ? (
                    <div className="dropoff-search-message">Searching places...</div>
                  ) : dropoffSearchText.trim().length >= 2 ? (
                    <div className="dropoff-search-message">
                      <span>No places found.</span>
                      <a href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(dropoffSearchText)}`} target="_blank" rel="noreferrer">
                        Search in Google Maps <ExternalLink size={13} />
                      </a>
                    </div>
                  ) : (
                    <div className="dropoff-search-message">Search by name or paste a Google Maps place link.</div>
                  )}
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowDropoffModal(false)}>Close</button>
              </div>
            </div>
          </div>
        )}

        {/* Compact progress state while the backend builds routes */}
        {isPlanning && (
          <section className="planning-page" role="status" aria-live="polite">
            <div className="planning-card">
              <div className="planning-card-topline">
                <span className="planning-state-label">
                  <span className="planning-live-indicator" />
                  In progress
                </span>
              </div>
              <h2>Optimizing routes</h2>
              <p className="planning-description">Assigning employees and calculating road travel times.</p>

              <div className="planning-progress-track" role="progressbar" aria-label="Route optimization in progress" aria-valuetext="In progress">
                <span />
              </div>

              <div className="planning-details">
                <div className="planning-detail">
                  <span>Roster</span>
                  <strong title={uploadedRosterFile.name}>{uploadedRosterFile.name}</strong>
                </div>
                <div className="planning-detail">
                  <span>Drop-off facility</span>
                  <strong title={destination?.name}>{destination?.name || "Confirmed facility"}</strong>
                  {destination?.lat != null && destination?.lon != null && (
                    <small>{Number(destination.lat).toFixed(4)}, {Number(destination.lon).toFixed(4)}</small>
                  )}
                </div>
              </div>
            </div>
          </section>
        )}

        {/* KPI Summary Cards */}
        {clustering && (
          <div className="kpi-row">
            <article className="kpi-card kpi-card--employees">
              <div className="kpi-card-head">
                <span className="kpi-title">Employees</span>
                <span className="kpi-icon"><Users size={17} /></span>
              </div>
              <div className="kpi-main">
                <span className="kpi-number">{clustering.total_employees}</span>
                <span className="kpi-unit">employees</span>
              </div>
              <div
                className="kpi-meter"
                role="progressbar"
                aria-label="Employees assigned to routes"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={assignmentRate}
                aria-valuetext={`${clustering.routed_employees} of ${clustering.total_employees} employees assigned`}
              >
                <span style={{ width: `${assignmentRate}%` }} />
              </div>
              <div className={`kpi-detail ${clustering.unverified_employees ? "is-warning" : "is-good"}`}>
                {clustering.routed_employees} of {clustering.total_employees} assigned
                {clustering.unverified_employees > 0 && <span> · {clustering.unverified_employees} need review</span>}
              </div>
            </article>

            <article className="kpi-card kpi-card--fleet">
              <div className="kpi-card-head">
                <span className="kpi-title">Planned routes</span>
                <span className="kpi-icon"><Bus size={18} /></span>
              </div>
              <div className="kpi-main">
                <span className="kpi-number">{clustering.total_buses}</span>
                <span className="kpi-unit">routes</span>
              </div>
              <div className="kpi-detail">Across shifts · fleet not assigned</div>
            </article>

            <article className="kpi-card kpi-card--stops">
              <div className="kpi-card-head">
                <span className="kpi-title">Route stops</span>
                <span className="kpi-icon"><MapPin size={17} /></span>
              </div>
              <div className="kpi-main">
                <span className="kpi-number">{clustering.total_stops}</span>
                <span className="kpi-unit">stops</span>
              </div>
              <div className="kpi-detail">Stops counted across all shifts</div>
            </article>

            <article className={`kpi-card kpi-card--time ${longestRoute && Number(longestRoute.longest_ride_minutes) > 70 ? "has-alert" : ""}`}>
              <div className="kpi-card-head">
                <span className="kpi-title">Longest ride</span>
                <span className="kpi-icon"><Clock3 size={17} /></span>
              </div>
              <div className="kpi-main">
                <span className="kpi-number">
                  {longestRoute ? Number(longestRoute.longest_ride_minutes).toFixed(1) : "—"}
                </span>
                <span className="kpi-unit">min</span>
              </div>
              <div className="kpi-detail">
                {longestRoute
                  ? `${longestRoute.bus_id}${Number(longestRoute.longest_ride_minutes) > 70 ? ` · ${Math.round(Number(longestRoute.longest_ride_minutes) - 70)} min over limit` : " · highest route time"}`
                  : "No routes available"}
              </div>
            </article>
          </div>
        )}

        {/* TAB 1: INTERACTIVE ROUTE MAP */}
        {activeTab === "map" && clustering && (
          <div className="map-workspace">
            {hoveredEmployee && (
              <div
                style={{
                  background: "#F8FAFC",
                  border: "1px solid #E2E8F0",
                  borderRadius: 8,
                  padding: "8px 14px",
                  marginBottom: 10,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <span style={{ fontWeight: 700, color: "var(--primary)" }}>
                    Inspecting: {hoveredEmployee.employee_name}
                  </span>
                  <span style={{ color: "var(--text-secondary)", marginLeft: 10, fontSize: "0.82rem" }}>
                    Pickup: <b>{hoveredEmployee.pickup_point}</b> &rarr; Assigned: <b>{hoveredEmployee.assigned_bus}</b> ({hoveredEmployee.shift})
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

        {/* TAB 2: BUS ROUTES GRID */}
        {activeTab === "routes" && clustering && (
          <section className="route-review-page">
            <header className="route-review-heading">
              <div>
                <h2>Review routes</h2>
                <p>Check journey times, then open a route to see its pickup list.</p>
              </div>
              <span className="route-count-pill">{clustering.routes.length} routes</span>
            </header>

            {routesNeedingReview.length > 0 ? (
              <div className="route-review-alert" role="status">
                <span className="route-review-alert-icon"><Clock3 size={17} /></span>
                <div>
                  <strong>{routesNeedingReview.length} {routesNeedingReview.length === 1 ? "route is" : "routes are"} over 70 minutes</strong>
                  <span>Review the flagged journeys before approving the roster.</span>
                </div>
              </div>
            ) : (
              <div className="route-review-clear" role="status">
                <Check size={17} /> All route estimates are within 70 minutes.
              </div>
            )}

            <div className="route-filter-row">
              <div className="route-shift-filter" aria-label="Filter routes by shift">
                <span>Show</span>
                {["ALL", "A", "B", "C"].map((s) => (
                  <button
                    key={s}
                    type="button"
                    className={`route-shift-option ${shiftFilter === s ? "active" : ""}`}
                    aria-pressed={shiftFilter === s}
                    onClick={() => setShiftFilter(s)}
                  >
                    {s === "ALL" ? "All shifts" : `Shift ${s}`}
                  </button>
                ))}
              </div>
              <details className="route-more-filters">
                <summary>More filters</summary>
                <label>
                  Pickup area
                  <select
                    className="select-field"
                    value={corridorFilter}
                    onChange={(e) => setCorridorFilter(e.target.value)}
                  >
                    {["ALL", ...Array.from(new Set(clustering.routes.map((r) => r.corridor).filter(Boolean)))].map((c) => (
                      <option key={c} value={c}>{c === "ALL" ? "All pickup areas" : c}</option>
                    ))}
                  </select>
                </label>
              </details>
            </div>

            <div className="routes-grid route-review-grid">
              {clustering.routes
                .filter((r) => shiftFilter === "ALL" || r.shift.includes(shiftFilter))
                .filter((r) => corridorFilter === "ALL" || r.corridor === corridorFilter)
                .map((route) => {
                  const rideMinutes = route.longest_ride_minutes == null || route.longest_ride_minutes === ""
                    ? Number.NaN
                    : Number(route.longest_ride_minutes);
                  const isOverLimit = Number.isFinite(rideMinutes) && rideMinutes > 70;
                  const stopCount = route.stops?.length || 0;
                  const passengerCount = Number(route.passengers_assigned) || 0;
                  return (
                    <article key={route.bus_id} className={`route-card route-review-card ${isOverLimit ? "needs-review" : ""}`}>
                      <div className="route-review-card-head">
                        <div className="route-review-name">
                          <span className="route-number">Route {route.route_no}</span>
                          <span className="route-shift-tag">Shift {route.shift}</span>
                        </div>
                        <span className={`route-status-tag ${isOverLimit ? "warning" : "good"}`}>
                          {Number.isFinite(rideMinutes) ? (isOverLimit ? "Needs review" : "Within limit") : "Time unavailable"}
                        </span>
                        <p className="route-corridor">{route.corridor}</p>
                      </div>

                      <div className="route-review-card-body">
                        <div className="route-review-stats">
                          <div><span>First pickup</span><strong>{route.first_pickup_time || "—"}</strong></div>
                          <div><span>At facility</span><strong>{route.plant_arrival_time || "—"}</strong></div>
                          <div className={isOverLimit ? "over-limit" : ""}>
                            <span>Longest ride</span>
                            <strong>{Number.isFinite(rideMinutes) ? `${rideMinutes.toFixed(1)} min` : "—"}</strong>
                          </div>
                        </div>

                        <div className="route-review-footer">
                          <span>{passengerCount} of 32 seats · {stopCount} pickup {stopCount === 1 ? "stop" : "stops"}</span>
                          <button
                            type="button"
                            className="route-map-link"
                            onClick={() => {
                              setActiveTab("map");
                              setSelectedBusId(route.bus_id);
                            }}
                          >
                            View on map <MapPin size={14} />
                          </button>
                        </div>

                        <details className="route-stops-disclosure">
                          <summary>View pickup list</summary>
                          <div className="route-stop-list">
                            {(route.stops || []).map((st) => (
                              <div key={st.seq} className="route-stop-row">
                                <div>
                                  <b>#{st.seq}</b>
                                  <span>{st.stop_name}</span>
                                  <small>{st.pickup_time || "Time unavailable"} · {st.demand} {Number(st.demand) === 1 ? "rider" : "riders"}</small>
                                  {st.employees?.length > 0 && (
                                    <small className="route-stop-passengers">{st.employees.map((e) => e.emp_name).join(", ")}</small>
                                  )}
                                </div>
                                <button
                                  type="button"
                                  aria-label={`Show ${st.stop_name} on map`}
                                  onClick={() => {
                                    setActiveTab("map");
                                    setSelectedBusId(route.bus_id);
                                  }}
                                >
                                  <MapPin size={14} />
                                </button>
                              </div>
                            ))}
                          </div>
                        </details>
                      </div>
                    </article>
                  );
                })}
            </div>
          </section>
        )}

        {/* TAB 3: EMPLOYEES ROSTER TABLE */}
        {activeTab === "employees" && clustering && (
          <div className="table-panel">
            <div className="table-filter-bar">
              <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                <div className="table-summary">
                  <strong>{filteredEmployees.length}</strong> of <strong>{clustering.employees.length}</strong> employees
                </div>
                <div className="seg">
                  {["ALL", "A", "B", "C"].map((s) => (
                    <button
                      key={s}
                      className={`seg-btn ${shiftFilter === s ? "active" : ""}`}
                      onClick={() => setShiftFilter(s)}
                    >
                      {s === "ALL" ? "All Shifts" : `Shift ${s}`}
                    </button>
                  ))}
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
                    <th>Emp ID</th>
                    <th>Name</th>
                    <th>Grade</th>
                    <th>Shift</th>
                    <th>Assigned Route</th>
                    <th>Pickup Stop</th>
                    <th>Mobile</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredEmployees.map((e, idx) => (
                    <tr key={idx}>
                      <td>#{e.employee_id}</td>
                      <td style={{ fontWeight: 600 }}>{e.employee_name}</td>
                      <td>{e.grade}</td>
                      <td>
                        <span className="badge-seats">{e.shift}</span>
                      </td>
                      <td>
                        <span className="bus-badge" style={{ fontWeight: 600 }}>
                          {e.assigned_bus}
                        </span>
                      </td>
                      <td>{e.pickup_point}</td>
                      <td style={{ fontFamily: "monospace", color: "var(--text-muted)" }}>
                        {e.mobile || "-"}
                      </td>
                      <td>
                        {e.latitude && e.longitude ? (
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => {
                              setHoveredEmployee(e);
                              setActiveTab("map");
                            }}
                          >
                            <Crosshair size={12} /> View Map
                          </button>
                        ) : (
                          <span style={{ fontSize: "0.75rem", color: "#DC2626", fontWeight: 600 }}>
                            Outlier / Review
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* TAB 4: THIS SUPERVISOR'S ROSTER HISTORY */}
        {activeTab === "history" && (
          <HistoryPanel currentUser={currentUser} getToken={getToken} />
        )}
      </main>

    </div>
  );
}
