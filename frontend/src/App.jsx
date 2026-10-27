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
  Info,
  ShieldCheck,
  Route as RouteIcon,
  Gauge,
} from "lucide-react";
import { exportToExcel } from "./utils/excel";
import { computeFleetCost } from "./utils/fleetCost";
import { exportRoutePdf } from "./utils/routePdf";
import RouteMap from "./components/RouteMap";
import AuthModal from "./components/AuthModal";
import HistoryPanel from "./components/HistoryPanel";
import { recordRosterHistory, authenticatedFetch } from "./utils/clerk";

const PRESET_FACILITIES = [
  {
    name: "JCB India Ltd, Talegaon",
    address: "Talegaon MIDC, Vadgaon, Pune, Maharashtra 410507",
    lat: 18.7765871,
    lon: 73.6947478,
    source: "Verified Plant Pin",
  },
  {
    name: "JCB Pune Plant (Talegaon Industrial Area)",
    address: "Plot No. A-1, Talegaon Industrial Area, Pune",
    lat: 18.7765871,
    lon: 73.6947478,
    source: "Verified Plant Pin",
  },
];

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
        if (!response.ok) {
          if (!cancelled) {
            setShiftStartTimes({ A: "06:50", B: "14:50", C: "22:50" });
            setArrivalBufferMinutes(10);
          }
          return;
        }
        const text = await response.text();
        if (!text) {
          if (!cancelled) {
            setShiftStartTimes({ A: "06:50", B: "14:50", C: "22:50" });
            setArrivalBufferMinutes(10);
          }
          return;
        }
        const payload = JSON.parse(text);
        const startTimes = payload.shift_start_times || { A: "06:50", B: "14:50", C: "22:50" };
        if (!cancelled) {
          setShiftStartTimes(startTimes);
          setArrivalBufferMinutes(Number(payload.arrival_buffer_minutes) || 10);
        }
      } catch {
        if (!cancelled) {
          setShiftStartTimes({ A: "06:50", B: "14:50", C: "22:50" });
          setArrivalBufferMinutes(10);
        }
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

  // Drop-off Location Search with instant fallback to verified facilities
  useEffect(() => {
    const controller = new AbortController();
    const query = (dropoffSearchText || "").trim().toLowerCase();

    // Find any local preset facility matches immediately
    const matchedPresets = PRESET_FACILITIES.filter(
      (f) =>
        !query ||
        f.name.toLowerCase().includes(query) ||
        f.address.toLowerCase().includes(query) ||
        "jcb talegaon pune plant factory".includes(query)
    );

    if (!query) {
      setDropoffCandidates(PRESET_FACILITIES);
      setDropoffSearchError("");
      setIsSearchingDropoff(false);
      return;
    }

    if (matchedPresets.length > 0) {
      setDropoffCandidates(matchedPresets);
    }

    setDropoffSearchError("");
    const timer = setTimeout(async () => {
      setIsSearchingDropoff(true);
      try {
        const res = await authenticatedFetch(
          `/api/dropoff/search?q=${encodeURIComponent(query)}`,
          { signal: controller.signal },
          getToken,
        );
        if (res.ok) {
          const payload = await res.json();
          if (Array.isArray(payload) && payload.length > 0) {
            setDropoffCandidates(payload);
          } else if (matchedPresets.length === 0) {
            setDropoffCandidates([]);
          }
        }
      } catch (err) {
        if (err.name !== "AbortError") {
          console.warn("Drop-off search warning:", err);
          if (matchedPresets.length === 0) {
            setDropoffCandidates(PRESET_FACILITIES);
          }
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
    setConfirmedDropoff(item);
    setClustering(null);
    setPlannedRosterFile(null);
    setDestination({
      name: item.name,
      lat: item.lat,
      lon: item.lon,
    });
    setDropoffCandidates([]);
    setDropoffSearchText("");
    setShowDropoffModal(false);
    setIsConfirmingDropoff(false);

    try {
      await authenticatedFetch("/api/dropoff/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: item.name,
          address: item.address,
          lat: item.lat,
          lon: item.lon,
          confirmed_by: "Supervisor (Web Dashboard)",
        }),
      }, getToken);
    } catch (err) {
      console.warn("Could not sync drop-off with backend history:", err);
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

  
  const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
  const shiftTimesValid = Object.keys(shiftStartTimes).length > 0 &&
    Object.values(shiftStartTimes).every((time) => TIME_RE.test(time || ""));
  const hasShiftChoice = Boolean(selectedTimeShift) && shiftTimesValid;

  const setupSteps = [
    { title: "Upload roster", hint: uploadedRosterFile ? uploadedRosterFile.name : "Excel .xlsx or .xls", done: Boolean(uploadedRosterFile) },
    { title: "Drop-off facility", hint: destination ? destination.name : "Search and confirm", done: Boolean(destination) },
    { title: "Shift and start time", hint: hasShiftChoice ? `Shift ${selectedTimeShift} · ${shiftStartTimes[selectedTimeShift]}` : "Pick a shift", done: hasShiftChoice },
    { title: "Build routes", hint: canBuildRoutes ? "Ready when you are" : "Complete the steps first", done: false },
  ];
  const currentStepIndex = Math.max(0, setupSteps.findIndex((step) => !step.done));

  const initials = (currentUser?.display_name || currentUser?.email || "S")
    .split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "S";

  const showStart = !clustering && !isPlanning && activeTab !== "history";
  const pageMeta = activeTab === "history"
    ? { title: "Roster history", sub: "Plans you have saved, ready to download again." }
    : !clustering
      ? { title: "Plan employee transport", sub: "Upload a roster, confirm the drop-off and build routes." }
      : activeTab === "routes"
        ? { title: "Route review", sub: "Check journey times, then open a route to see its pickup list." }
        : activeTab === "employees"
          ? { title: "Employees", sub: "Everyone on the roster with their route and pickup stop." }
          : { title: "Route map", sub: "Explore every bus route, stop and pickup on the map." };

  const unverifiedCount = clustering ? clustering.employees.filter(
    (e) => e.location_status === "LOCATION_UNVERIFIED" || e.assigned_bus.includes("Outlier")
  ).length : 0;

  const goTo = (tab) => { setActiveTab(tab); setMobileMenuOpen(false); };
  const startNewRoster = () => fileInputRef.current?.click();

  if (!isLoaded || isSwitchingAccount) {
    return <div className="auth-loading-screen"><span className="spinner" /> Checking supervisor session…</div>;
  }

  if (!isSignedIn) return <AuthModal />;

  const navItems = [
    { id: "map", label: "Route map", icon: MapPin, needsData: true },
    { id: "routes", label: "Route review", icon: Bus, needsData: true, badge: clustering?.routes.length },
    { id: "employees", label: "Employees", icon: Users, needsData: true, badge: clustering?.employees.length },
    { id: "history", label: "Roster history", icon: History },
  ];

  return (
    <div className="app-layout">
      <input
        type="file"
        ref={fileInputRef}
        accept=".xlsx, .xls"
        style={{ display: "none" }}
        onChange={handleFileUpload}
      />

      {/* Mobile top bar */}
      <header className="mobile-header">
        <div className="mobile-brand">
          <span className="brand-mark"><Bus size={16} /></span>
          <span>AutoRoster</span>
        </div>
        <button
          type="button"
          className="mobile-menu-btn"
          onClick={() => setMobileMenuOpen((prev) => !prev)}
          aria-label="Toggle navigation menu"
        >
          {mobileMenuOpen ? <X size={18} /> : <Menu size={18} />}
        </button>
      </header>

      <div className={`sidebar-backdrop ${mobileMenuOpen ? "open" : ""}`} onClick={() => setMobileMenuOpen(false)} />

      {/* Sidebar */}
      <aside className={`sidebar ${mobileMenuOpen ? "open" : ""}`}>
        <div className="sidebar-brand">
          <div className="brand">
            <span className="brand-mark"><Bus size={19} /></span>
            <span className="brand-text">
              <span className="brand-name">AutoRoster</span>
              <span className="brand-sub">Employee Transport</span>
            </span>
          </div>
          <button type="button" className="sidebar-close-btn" onClick={() => setMobileMenuOpen(false)} aria-label="Close sidebar">
            <X size={16} />
          </button>
        </div>

        <div>
          <div className="nav-label">Workspace</div>
          <nav className="sidebar-nav">
            {navItems.map(({ id, label, icon: Icon, needsData, badge }) => (
              <button
                key={id}
                type="button"
                className={`nav-item ${activeTab === id && (!needsData || clustering) ? "active" : ""}`}
                disabled={needsData && !clustering}
                title={needsData && !clustering ? "Build routes to unlock this view" : label}
                onClick={() => goTo(id)}
              >
                <Icon size={18} />
                <span>{label}</span>
                {badge != null && <span className="nav-badge">{badge}</span>}
              </button>
            ))}
          </nav>
        </div>

        <div className="sidebar-footer">
          {currentUser && (
            <div className="user-card">
              <span className="user-avatar">{initials}</span>
              <div className="user-meta">
                <strong title={currentUser.email}>{currentUser.display_name}</strong>
                <span><ShieldCheck size={12} /> Supervisor</span>
              </div>
              <button type="button" className="icon-btn" onClick={handleSignOut} title="Sign out" aria-label="Sign out">
                <LogOut size={16} />
              </button>
            </div>
          )}
        </div>
      </aside>

      {/* Main */}
      <main className="main-content">
        <div className="topbar">
          <div>
            <h1 className="page-title">{pageMeta.title}</h1>
            <p className="page-sub">{pageMeta.sub}</p>
          </div>
          {clustering && activeTab !== "history" && (
            <div className="topbar-actions">
              <button type="button" className="btn btn-secondary" onClick={startNewRoster}>
                <Upload size={15} /> New roster
              </button>
              <button type="button" className="btn btn-secondary" onClick={() => runOptimization()} disabled={isOptimising}>
                <RefreshCw size={15} className={isOptimising ? "spin" : ""} />
                {isOptimising ? "Optimizing…" : "Re-run"}
              </button>
              <button type="button" className="btn btn-primary" onClick={handleDownloadFinalExcel}>
                <Download size={15} /> Download Excel
              </button>
            </div>
          )}
        </div>

        {activeTab !== "history" && (
          <section className="setup-bar" aria-label="Planning setup">
            <div className="setup-field setup-field--facility">
              <span className={`setup-icon ${destination ? "" : "is-empty"}`}><MapPin size={18} /></span>
              <div className="setup-text">
                <span className="setup-label">Drop-off facility</span>
                <strong title={destination?.name}>{destination ? destination.name : "Not chosen yet"}</strong>
                {destination && <small>{destination.lat.toFixed(4)}, {destination.lon.toFixed(4)}</small>}
              </div>
              <button type="button" className="btn btn-soft btn-sm" onClick={openDropoffPicker}>
                <Search size={13} /> {destination ? "Change" : "Choose"}
              </button>
            </div>

            {destination && Object.keys(shiftStartTimes).length > 0 && (
              <>
                <label className="setup-field">
                  <div className="setup-stack">
                    <span className="setup-label">Shift</span>
                    <select
                      className="select-field"
                      value={selectedTimeShift}
                      onChange={(event) => { setSelectedTimeShift(event.target.value); setStatusMessage(""); }}
                      disabled={isOptimising}
                      aria-label="Select shift to set its start time"
                    >
                      <option value="">Select shift</option>
                      {Object.keys(shiftStartTimes).sort().map((shift) => (
                        <option key={shift} value={shift}>Shift {shift}</option>
                      ))}
                    </select>
                  </div>
                </label>
                <label className="setup-field">
                  <div className="setup-stack">
                    <span className="setup-label">Shift starts at</span>
                    <input
                      type="time"
                      required
                      className="input-field"
                      value={shiftStartTimes[selectedTimeShift] || ""}
                      onChange={(event) => {
                        setShiftStartTimes((current) => ({ ...current, [selectedTimeShift]: event.target.value }));
                        setStatusMessage("");
                      }}
                      disabled={isOptimising || !selectedTimeShift}
                      aria-label={`Start time for Shift ${selectedTimeShift}`}
                    />
                  </div>
                  {needsShiftTimeRerun && clustering && <span className="pill pill-warn">Re-run to apply</span>}
                </label>
                <div className="setup-note">
                  <Info size={15} />
                  Routes arrive {arrivalBufferMinutes ?? 0} min before the selected shift starts.
                </div>
              </>
            )}
          </section>
        )}

        {statusMessage.startsWith("Error:") && !isPlanning && (
          <div className="banner banner-error" role="alert">
            <AlertTriangle size={16} />
            <div>{statusMessage}</div>
          </div>
        )}
        {statusMessage && !statusMessage.startsWith("Error:") && !isPlanning && (
          <div className="banner banner-warn" role="status">
            <Info size={16} />
            <div>{statusMessage}</div>
          </div>
        )}

        {/* Start state */}
        {showStart && (
          <section className="start">
            <ol className="stepper" aria-label="Planning steps">
              {setupSteps.map((step, index) => (
                <li
                  key={step.title}
                  className={`step ${step.done ? "is-done" : ""} ${index === currentStepIndex ? "is-current" : ""}`}
                >
                  <span className="step-dot">{step.done ? <Check size={14} /> : index + 1}</span>
                  <span className="step-text">
                    <strong>{step.title}</strong>
                    <small title={step.hint}>{step.hint}</small>
                  </span>
                </li>
              ))}
            </ol>

            <div
              className={`dropzone ${isRosterDragOver ? "is-dragging" : ""} ${uploadedRosterFile ? "is-compact" : ""}`}
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
              onClick={startNewRoster}
              role="button"
              tabIndex={0}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  startNewRoster();
                }
              }}
            >
              <div className="dropzone-icon"><FileText size={uploadedRosterFile ? 20 : 26} /></div>
              <div>
                <h3>{uploadedRosterFile ? "Replace the roster file" : "Drop your Excel roster here"}</h3>
                <p>or <span>browse files</span> on your computer</p>
                {!uploadedRosterFile && (
                  <div className="dropzone-meta">
                    <span className="pill pill-teal"><CheckCircle2 size={12} /> Excel workbook</span>
                    <span className="pill">.xlsx · .xls</span>
                  </div>
                )}
              </div>
            </div>

            {hasPendingRoster && (
              <section className="ready-card" aria-label="Build routes from selected roster">
                <div className="ready-file">
                  <span className="ready-icon"><FileText size={19} /></span>
                  <div>
                    <strong title={uploadedRosterFile.name}>{uploadedRosterFile.name}</strong>
                    <span className={canBuildRoutes ? "is-ready" : ""}>
                      {!destination
                        ? "Choose a drop-off facility to continue."
                        : !Object.keys(shiftStartTimes).length
                          ? "Loading shift options…"
                          : !selectedTimeShift
                            ? "Select a shift to review its start time."
                            : !shiftTimesValid
                              ? "Enter a valid start time for every shift."
                              : "Roster ready to plan."}
                    </span>
                  </div>
                </div>
                <div className="ready-actions">
                  <button type="button" className="btn btn-ghost" onClick={() => setUploadedRosterFile(null)}>Remove</button>
                  <button type="button" className="btn btn-primary" onClick={() => runOptimization()} disabled={!canBuildRoutes}>
                    <RouteIcon size={16} /> Build routes
                  </button>
                </div>
              </section>
            )}


          </section>
        )}

        {/* Drop-off picker */}
        {showDropoffModal && (
          <div className="modal-overlay" onClick={() => setShowDropoffModal(false)}>
            <div className="modal-container" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="Choose drop-off facility">
              <div className="modal-header">
                <h3 className="modal-title">
                  <span className="modal-title-icon"><MapPin size={17} /></span> Choose drop-off facility
                </h3>
                <button type="button" className="icon-btn" onClick={() => setShowDropoffModal(false)} aria-label="Close search">
                  <X size={18} />
                </button>
              </div>

              <div className="modal-body">
                <div className="dropoff-search-field">
                  <Search size={17} />
                  <input
                    type="text"
                    placeholder="Search a place or paste a Google Maps link"
                    value={dropoffSearchText}
                    onChange={(event) => { setDropoffSearchText(event.target.value); setDropoffCandidates([]); }}
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
                    <div className="dropoff-search-message">Searching places…</div>
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

        {/* Planning in progress */}
        {isPlanning && (
          <section className="planning-page" role="status" aria-live="polite">
            <div className="card planning-card">
              <span className="planning-state"><span className="planning-live" /> In progress</span>
              <h2>Optimizing routes</h2>
              <p className="planning-desc">Assigning employees and calculating road travel times. This can take a little while for large rosters.</p>
              <div className="progress-track" role="progressbar" aria-label="Route optimization in progress" aria-valuetext="In progress"><span /></div>
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

        {/* KPIs */}
        {clustering && activeTab !== "history" && (
          <div className="kpi-row">
            <article className="kpi-card kpi--employees">
              <div className="kpi-head">
                <span className="kpi-title">Employees</span>
                <span className="kpi-icon"><Users size={16} /></span>
              </div>
              <div className="kpi-main">
                <span className="kpi-number">{clustering.total_employees}</span>
                <span className="kpi-unit">total</span>
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
                {clustering.routed_employees} assigned
                {clustering.unverified_employees > 0 && ` · ${clustering.unverified_employees} need review`}
              </div>
            </article>

            <article className="kpi-card kpi--routes">
              <div className="kpi-head">
                <span className="kpi-title">Planned routes</span>
                <span className="kpi-icon"><Bus size={16} /></span>
              </div>
              <div className="kpi-main">
                <span className="kpi-number">{clustering.total_buses}</span>
                <span className="kpi-unit">routes</span>
              </div>
              <div className="kpi-detail">Across shifts · fleet not assigned</div>
            </article>

            <article className="kpi-card kpi--stops">
              <div className="kpi-head">
                <span className="kpi-title">Pickup stops</span>
                <span className="kpi-icon"><MapPin size={16} /></span>
              </div>
              <div className="kpi-main">
                <span className="kpi-number">{clustering.total_stops}</span>
                <span className="kpi-unit">stops</span>
              </div>
              <div className="kpi-detail">Counted across all shifts</div>
            </article>

            <article className="kpi-card kpi--km">
              <div className="kpi-head">
                <span className="kpi-title">Fleet distance</span>
                <span className="kpi-icon"><Gauge size={16} /></span>
              </div>
              <div className="kpi-main">
                <span className="kpi-number">{clustering.total_fleet_km}</span>
                <span className="kpi-unit">km</span>
              </div>
              <div className="kpi-detail">Sum of all route distances</div>
            </article>

            <article className={`kpi-card kpi--time ${longestRoute && Number(longestRoute.longest_ride_minutes) > 70 ? "has-alert" : ""}`}>
              <div className="kpi-head">
                <span className="kpi-title">Longest ride</span>
                <span className="kpi-icon"><Clock3 size={16} /></span>
              </div>
              <div className="kpi-main">
                <span className="kpi-number">
                  {longestRoute && Number.isFinite(Number(longestRoute.longest_ride_minutes)) ? Number(longestRoute.longest_ride_minutes).toFixed(1) : "—"}
                </span>
                <span className="kpi-unit">min</span>
              </div>
              <div className="kpi-detail" title={longestRoute?.bus_id}>
                {longestRoute
                  ? `${longestRoute.bus_id}${Number(longestRoute.longest_ride_minutes) > 70 ? ` · ${Math.round(Number(longestRoute.longest_ride_minutes) - 70)} min over limit` : " · highest route time"}`
                  : "No routes available"}
              </div>
            </article>
          </div>
        )}

        {/* Map */}
        {activeTab === "map" && clustering && (
          <div className="map-workspace">
            {hoveredEmployee && (
              <div className="inspect-bar">
                <div>
                  <strong>Inspecting: {hoveredEmployee.employee_name}</strong>
                  <span>
                    Pickup <b>{hoveredEmployee.pickup_point}</b> &rarr; <b>{hoveredEmployee.assigned_bus}</b> ({hoveredEmployee.shift})
                  </span>
                </div>
                <button className="btn btn-secondary btn-sm" onClick={() => setHoveredEmployee(null)}>Clear selection</button>
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

        {/* Route review */}
        {activeTab === "routes" && clustering && (
          <section className="route-review-page">
            {routesNeedingReview.length > 0 ? (
              <div className="banner banner-warn" role="status">
                <Clock3 size={17} />
                <div>
                  <strong>{routesNeedingReview.length} {routesNeedingReview.length === 1 ? "route is" : "routes are"} over 70 minutes</strong>
                  <span>Review the flagged journeys before approving the roster.</span>
                </div>
              </div>
            ) : (
              <div className="banner banner-ok" role="status">
                <CheckCircle2 size={17} /> All route estimates are within 70 minutes.
              </div>
            )}

            <div className="route-filter-row">
              <div className="route-filter-left">
                <div className="seg" aria-label="Filter routes by shift">
                  {["ALL", "A", "B", "C"].map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={`seg-btn ${shiftFilter === s ? "active" : ""}`}
                      aria-pressed={shiftFilter === s}
                      onClick={() => setShiftFilter(s)}
                    >
                      {s === "ALL" ? "All shifts" : `Shift ${s}`}
                    </button>
                  ))}
                </div>
                <select className="select-field" value={corridorFilter} onChange={(e) => setCorridorFilter(e.target.value)} aria-label="Filter by pickup area">
                  {["ALL", ...Array.from(new Set(clustering.routes.map((r) => r.corridor).filter(Boolean)))].map((c) => (
                    <option key={c} value={c}>{c === "ALL" ? "All pickup areas" : c}</option>
                  ))}
                </select>
              </div>
              <span className="pill pill-accent">{clustering.routes.length} routes</span>
            </div>

            <div className="route-review-grid">
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
                  const loadPct = Math.min(100, Math.round((passengerCount / 32) * 100));
                  return (
                    <article key={route.bus_id} className={`route-card ${isOverLimit ? "needs-review" : ""}`}>
                      <div className="route-card-head">
                        <div className="route-card-title">
                          <span className="route-number">Route {route.route_no}</span>
                          <span className="pill pill-accent">Shift {route.shift}</span>
                          <span className={`pill ${Number.isFinite(rideMinutes) ? (isOverLimit ? "pill-warn" : "pill-ok") : ""}`}>
                            {Number.isFinite(rideMinutes) ? (isOverLimit ? "Needs review" : "Within limit") : "Time unavailable"}
                          </span>
                        </div>
                        <p className="route-corridor">{route.corridor}</p>
                      </div>

                      <div className="route-card-body">
                        <div className="route-stats">
                          <div><span>First pickup</span><strong>{route.first_pickup_time || "—"}</strong></div>
                          <div><span>At facility</span><strong>{route.plant_arrival_time || "—"}</strong></div>
                          <div className={isOverLimit ? "over-limit" : ""}>
                            <span>Longest ride</span>
                            <strong>{Number.isFinite(rideMinutes) ? `${rideMinutes.toFixed(1)} min` : "—"}</strong>
                          </div>
                        </div>

                        <div className="load-row">
                          <div className="load-text">
                            <span><strong>{passengerCount}</strong> of 32 seats</span>
                            <span>{stopCount} pickup {stopCount === 1 ? "stop" : "stops"}</span>
                          </div>
                          <div className={`load-bar ${loadPct >= 95 ? "is-full" : ""}`}><span style={{ width: `${loadPct}%` }} /></div>
                        </div>

                        <div className="route-actions">
                          <button
                            type="button"
                            className="route-map-link"
                            onClick={() => { setActiveTab("map"); setSelectedBusId(route.bus_id); }}
                          >
                            <MapPin size={14} /> View on map
                          </button>
                          <button type="button" className="btn btn-ghost btn-sm" onClick={() => handleRoutePdf(route)}>
                            <FileText size={13} /> PDF
                          </button>
                        </div>

                        <details className="route-stops-disclosure">
                          <summary>Pickup list</summary>
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
                                  onClick={() => { setActiveTab("map"); setSelectedBusId(route.bus_id); }}
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

        {/* Employees */}
        {activeTab === "employees" && clustering && (
          <div className="table-panel">
            <div className="table-filter-bar">
              <div className="table-filter-left">
                <div className="table-summary">
                  <strong>{filteredEmployees.length}</strong> of <strong>{clustering.employees.length}</strong> employees
                </div>
                <div className="seg">
                  {["ALL", "A", "B", "C"].map((s) => (
                    <button key={s} className={`seg-btn ${shiftFilter === s ? "active" : ""}`} onClick={() => setShiftFilter(s)}>
                      {s === "ALL" ? "All shifts" : `Shift ${s}`}
                    </button>
                  ))}
                </div>
                <div className="seg">
                  <button className={`seg-btn ${employeeStatusFilter === "ALL" ? "active" : ""}`} onClick={() => setEmployeeStatusFilter("ALL")}>
                    Everyone
                  </button>
                  <button className={`seg-btn ${employeeStatusFilter === "UNVERIFIED" ? "active" : ""}`} onClick={() => setEmployeeStatusFilter("UNVERIFIED")}>
                    Needs review{unverifiedCount > 0 ? ` (${unverifiedCount})` : ""}
                  </button>
                </div>
              </div>
              <div className="search-wrap">
                <Search size={15} />
                <input
                  type="text"
                  className="search-box"
                  placeholder="Search employee, stop, or route…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />
              </div>
            </div>

            <div className="table-scroll">
              {filteredEmployees.length === 0 ? (
                <div className="empty-state">
                  <span className="empty-icon"><Users size={24} /></span>
                  <h4>No employees match</h4>
                  <p>Try a different shift, clear the search or switch the review filter.</p>
                </div>
              ) : (
                <table className="custom-table">
                  <thead>
                    <tr>
                      <th>Emp ID</th>
                      <th>Name</th>
                      <th>Grade</th>
                      <th>Shift</th>
                      <th>Assigned route</th>
                      <th>Pickup stop</th>
                      <th>Mobile</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredEmployees.map((e, idx) => (
                      <tr key={idx}>
                        <td className="cell-mono">#{e.employee_id}</td>
                        <td className="cell-strong">{e.employee_name}</td>
                        <td className="cell-muted">{e.grade}</td>
                        <td><span className="pill pill-accent">{e.shift}</span></td>
                        <td>
                          {e.assigned_bus.includes("Outlier")
                            ? <span className="pill pill-bad" title={e.resolution_reason}>{e.assigned_bus}</span>
                            : <span className="pill pill-teal">{e.assigned_bus}</span>}
                        </td>
                        <td>{e.pickup_point}</td>
                        <td className="cell-mono">{e.mobile || "-"}</td>
                        <td>
                          {e.latitude && e.longitude ? (
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={() => { setHoveredEmployee(e); setActiveTab("map"); }}
                            >
                              <Crosshair size={12} /> View on map
                            </button>
                          ) : (
                            <span className="pill pill-bad" title={e.resolution_reason}>Outlier / review</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )}

        {/* History */}
        {activeTab === "history" && (
          <HistoryPanel currentUser={currentUser} getToken={getToken} />
        )}
      </main>
    </div>
  );
}
