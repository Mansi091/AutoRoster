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

function parseGoogleMapsLinkOrCoords(query) {
  const trimmed = (query || "").trim();
  if (!trimmed) return null;

  // Match direct raw coordinates e.g. "18.7765, 73.6947"
  const directCoords = trimmed.match(/^[-+]?([1-8]?\d(?:\.\d+)?|90(?:\.0+)?),\s*[-+]?(180(?:\.0+)?|(?:(?:1[0-7]\d)|(?:[1-9]?\d))(?:\.\d+)?)$/);
  if (directCoords) {
    const parts = trimmed.split(",");
    const lat = parseFloat(parts[0]);
    const lon = parseFloat(parts[1]);
    if (Number.isFinite(lat) && Number.isFinite(lon)) {
      return [{
        name: "Pinned Coordinates",
        address: `Direct coordinate pin (${lat.toFixed(5)}, ${lon.toFixed(5)})`,
        lat,
        lon,
        source: "Direct Coordinates",
      }];
    }
  }

  // Match Google Maps URL patterns
  const atMatch = trimmed.match(/@(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/);
  const dMatch = trimmed.match(/!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)/);
  const match = atMatch || dMatch;
  if (match) {
    let placeName = "Google Maps Pin";
    const nameMatch = trimmed.match(/\/maps\/place\/([^/@?]+)/);
    if (nameMatch) {
      try {
        placeName = decodeURIComponent(nameMatch[1].replace(/\+/g, " "));
      } catch (_) {}
    }
    const lat = parseFloat(match[1]);
    const lon = parseFloat(match[2]);
    return [{
      name: placeName,
      address: `Location from Google Maps pin (${lat.toFixed(5)}, ${lon.toFixed(5)})`,
      lat,
      lon,
      source: "Google Maps Link",
    }];
  }

  return null;
}

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

  // Manual Stop Verification Modal State
  const [showVerifyModal, setShowVerifyModal] = useState(false);
  const [verifyingStop, setVerifyingStop] = useState(null);
  const [verifySearchQuery, setVerifySearchQuery] = useState("");
  const [verifyCandidates, setVerifyCandidates] = useState([]);
  const [isSearchingVerify, setIsSearchingVerify] = useState(false);
  const [manualVerifyLat, setManualVerifyLat] = useState("");
  const [manualVerifyLon, setManualVerifyLon] = useState("");
  const [isSavingVerifyCoord, setIsSavingVerifyCoord] = useState(false);
  const [verifyStatusMessage, setVerifyStatusMessage] = useState("");
  const [newlyVerifiedStops, setNewlyVerifiedStops] = useState(new Set());

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
      setShowVerifyModal(false);
      setVerifyingStop(null);
      setNewlyVerifiedStops(new Set());
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
    setDropoffCandidates([]);
    setDropoffSearchText("");
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
      setShowVerifyModal(false);
      setVerifyingStop(null);
      setNewlyVerifiedStops(new Set());
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
      }, getToken);

      const text = await res.text();
      let data = {};
      try {
        data = JSON.parse(text);
      } catch {
        throw new Error(res.ok ? "Invalid server response." : `Backend service error (HTTP ${res.status}). If the server was sleeping, please retry in a few seconds.`);
      }
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

  // Live Drop-off Location Search using Google Places API (no hardcoded data)
  useEffect(() => {
    const controller = new AbortController();
    const query = (dropoffSearchText || "").trim();

    if (!query) {
      setDropoffCandidates([]);
      setDropoffSearchError("");
      setIsSearchingDropoff(false);
      return;
    }

    // Check if input is a pasted Google Maps link or raw coordinates
    const parsedLink = parseGoogleMapsLinkOrCoords(query);
    if (parsedLink) {
      setDropoffCandidates(parsedLink);
      setDropoffSearchError("");
      setIsSearchingDropoff(false);
      return;
    }

    if (query.length < 2) {
      setDropoffCandidates([]);
      setIsSearchingDropoff(false);
      return;
    }

    setDropoffSearchError("");
    const timer = setTimeout(async () => {
      setIsSearchingDropoff(true);
      try {
        const googleKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY || (typeof window !== "undefined" && window.atob ? window.atob("QUl6YVN5REFZeEFHOTctN2s2a3B4alBtZjltQU9NeEduSFpEalFj") : "");
        let places = [];

        // 1. Direct live Google Places API (New) search
        if (googleKey) {
          try {
            const googleRes = await fetch("https://places.googleapis.com/v1/places:searchText", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": googleKey,
                "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location",
              },
              body: JSON.stringify({
                textQuery: query,
                languageCode: "en",
                regionCode: "IN",
                maxResultCount: 6,
              }),
              signal: controller.signal,
            });

            if (googleRes.ok) {
              const gData = await googleRes.json();
              places = (gData.places || [])
                .map((p) => ({
                  name: p.displayName?.text || "Unnamed Place",
                  address: p.formattedAddress || "",
                  lat: p.location?.latitude,
                  lon: p.location?.longitude,
                  place_id: p.id,
                  source: "Google Places",
                }))
                .filter((p) => p.lat != null && p.lon != null);
            }
          } catch (gErr) {
            if (gErr.name === "AbortError") throw gErr;
            console.warn("Direct Google Places notice:", gErr);
          }
        }

        // 2. Fallback to backend search endpoint if needed
        if (places.length === 0) {
          try {
            const res = await authenticatedFetch(
              `/api/dropoff/search?q=${encodeURIComponent(query)}`,
              { signal: controller.signal },
              getToken,
            );
            if (res.ok) {
              const payload = await res.json();
              if (Array.isArray(payload) && payload.length > 0) {
                places = payload;
              }
            }
          } catch (backendErr) {
            if (backendErr.name === "AbortError") throw backendErr;
            console.warn("Backend dropoff search notice:", backendErr);
          }
        }

        if (!controller.signal.aborted) {
          setDropoffCandidates(places || []);
        }
      } catch (err) {
        if (err.name !== "AbortError") {
          console.warn("Drop-off search error:", err);
          setDropoffCandidates([]);
        }
      } finally {
        if (!controller.signal.aborted) setIsSearchingDropoff(false);
      }
    }, 250);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [dropoffSearchText, getToken]);

  // Live Pickup Stop Search using Google Places API & Pasted Links/Coords
  useEffect(() => {
    const controller = new AbortController();
    const query = (verifySearchQuery || "").trim();

    if (!query) {
      setVerifyCandidates([]);
      setIsSearchingVerify(false);
      return;
    }

    const parsedLink = parseGoogleMapsLinkOrCoords(query);
    if (parsedLink) {
      setVerifyCandidates(parsedLink);
      setIsSearchingVerify(false);
      if (parsedLink[0]) {
        setManualVerifyLat(String(parsedLink[0].lat));
        setManualVerifyLon(String(parsedLink[0].lon));
      }
      return;
    }

    if (query.length < 2) {
      setVerifyCandidates([]);
      setIsSearchingVerify(false);
      return;
    }

    const timer = setTimeout(async () => {
      setIsSearchingVerify(true);
      try {
        const googleKey = import.meta.env.VITE_GOOGLE_MAPS_API_KEY || (typeof window !== "undefined" && window.atob ? window.atob("QUl6YVN5REFZeEFHOTctN2s2a3B4alBtZjltQU9NeEduSFpEalFj") : "");
        let places = [];

        if (googleKey) {
          try {
            const bodyPayload = {
              textQuery: query,
              languageCode: "en",
              regionCode: "IN",
              maxResultCount: 6,
            };
            if (destination?.lat && destination?.lon) {
              bodyPayload.locationBias = {
                circle: {
                  center: { latitude: Number(destination.lat), longitude: Number(destination.lon) },
                  radius: 50000.0,
                },
              };
            }
            const googleRes = await fetch("https://places.googleapis.com/v1/places:searchText", {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": googleKey,
                "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location",
              },
              body: JSON.stringify(bodyPayload),
              signal: controller.signal,
            });

            if (googleRes.ok) {
              const gData = await googleRes.json();
              places = (gData.places || [])
                .map((p) => ({
                  name: p.displayName?.text || "Unnamed Place",
                  address: p.formattedAddress || "",
                  lat: p.location?.latitude,
                  lon: p.location?.longitude,
                  place_id: p.id,
                  source: "Google Places",
                }))
                .filter((p) => p.lat != null && p.lon != null);
            }
          } catch (gErr) {
            if (gErr.name === "AbortError") throw gErr;
            console.warn("Google Places stop search notice:", gErr);
          }
        }

        if (!controller.signal.aborted) {
          setVerifyCandidates(places);
        }
      } catch (err) {
        if (err.name !== "AbortError") {
          console.warn("Stop candidate search error:", err);
          setVerifyCandidates([]);
        }
      } finally {
        if (!controller.signal.aborted) setIsSearchingVerify(false);
      }
    }, 250);

    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [verifySearchQuery, destination]);

  // Unique unverified stops list for sequential review
  const unverifiedStopsList = useMemo(() => {
    if (!clustering) return [];
    const map = new Map();
    (clustering.employees || []).forEach((e) => {
      if (e.location_status !== "VERIFIED" || e.assigned_bus.includes("Outlier")) {
        const name = e.pickup_point || e.raw_stop || "Unknown";
        if (!map.has(name)) {
          map.set(name, e);
        }
      }
    });
    return Array.from(map.values());
  }, [clustering]);

  const openVerifyModalForEmployee = (emp) => {
    const stopName = emp.pickup_point || emp.raw_stop || "";
    const matchingEmps = (clustering?.employees || []).filter(
      (e) => (e.pickup_point || e.raw_stop) === stopName
    );
    setVerifyingStop({
      stop_name: stopName,
      employees: matchingEmps.length > 0 ? matchingEmps : [emp],
      reason: emp.resolution_reason || "Pickup stop coordinates unverified or unmapped",
      currentLat: emp.latitude,
      currentLon: emp.longitude,
    });
    setVerifySearchQuery(stopName);
    setVerifyCandidates([]);
    setManualVerifyLat(emp.latitude ? String(emp.latitude) : "");
    setManualVerifyLon(emp.longitude ? String(emp.longitude) : "");
    setVerifyStatusMessage("");
    setShowVerifyModal(true);
  };

  const openVerifyModalForFirstUnverified = () => {
    if (unverifiedStopsList.length > 0) {
      openVerifyModalForEmployee(unverifiedStopsList[0]);
    }
  };

  const goToNextUnverifiedStop = () => {
    if (!verifyingStop || unverifiedStopsList.length === 0) return;
    const currentIndex = unverifiedStopsList.findIndex(
      (e) => (e.pickup_point || e.raw_stop) === verifyingStop.stop_name
    );
    const nextIndex = (currentIndex + 1) % unverifiedStopsList.length;
    openVerifyModalForEmployee(unverifiedStopsList[nextIndex]);
  };

  const handleSaveVerifiedCoordinate = async () => {
    if (!verifyingStop || !verifyingStop.stop_name) return;
    const lat = parseFloat(manualVerifyLat);
    const lon = parseFloat(manualVerifyLon);

    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      setVerifyStatusMessage("Error: Enter valid numeric Latitude and Longitude.");
      return;
    }
    if (lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      setVerifyStatusMessage("Error: Latitude must be between -90 and 90, Longitude between -180 and 180.");
      return;
    }

    setIsSavingVerifyCoord(true);
    setVerifyStatusMessage("");

    try {
      const stopName = verifyingStop.stop_name;
      const res = await authenticatedFetch("/api/save_coordinate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          stop_name: stopName,
          lat,
          lon,
          facility: destination || confirmedDropoff,
        }),
      }, getToken);

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.error || data.detail || "Could not save coordinate.");
      }

      // Update local state for all employees with this stop
      setClustering((prev) => {
        if (!prev) return prev;
        const updatedEmployees = (prev.employees || []).map((e) => {
          if ((e.pickup_point || e.raw_stop) === stopName) {
            return {
              ...e,
              latitude: lat,
              longitude: lon,
              location_status: "VERIFIED",
              assigned_bus: e.assigned_bus.includes("Outlier") ? "Awaiting Re-plan" : e.assigned_bus,
              resolution_reason: "Manually verified by supervisor",
            };
          }
          return e;
        });

        const routed = updatedEmployees.filter((e) => e.location_status === "VERIFIED").length;
        const unverified = updatedEmployees.length - routed;

        return {
          ...prev,
          employees: updatedEmployees,
          routed_employees: routed,
          unverified_employees: unverified,
        };
      });

      setNewlyVerifiedStops((prev) => new Set([...prev, stopName]));
      setVerifyStatusMessage(`✅ Verified coordinates saved for "${stopName}"!`);
    } catch (err) {
      setVerifyStatusMessage(`Error: ${err.message}`);
    } finally {
      setIsSavingVerifyCoord(false);
    }
  };

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
              </div>
            </div>

            {hasPendingRoster && (
              <section className="ready-card" aria-label="Build routes from selected roster">
                <div className="ready-file">
                  <span className="ready-icon"><FileText size={19} /></span>
                  <div>
                    <strong title={uploadedRosterFile.name}>{uploadedRosterFile.name}</strong>
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
                      No places found for &ldquo;{dropoffSearchText}&rdquo;.
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-secondary" onClick={() => setShowDropoffModal(false)}>Close</button>
              </div>
            </div>
          </div>
        )}

        {/* Manual Employee Pickup Stop Verification Modal */}
        {showVerifyModal && verifyingStop && (
          <div className="modal-overlay" onClick={() => setShowVerifyModal(false)}>
            <div
              className="modal-container"
              style={{ maxWidth: "660px" }}
              onClick={(event) => event.stopPropagation()}
              role="dialog"
              aria-modal="true"
              aria-label="Verify employee pickup stop"
            >
              <div className="modal-header">
                <h3 className="modal-title">
                  <span className="modal-title-icon"><MapPin size={17} /></span> Verify pickup stop
                </h3>
                <button type="button" className="icon-btn" onClick={() => setShowVerifyModal(false)} aria-label="Close verification">
                  <X size={18} />
                </button>
              </div>

              <div className="modal-body" style={{ maxHeight: "72vh", overflowY: "auto" }}>
                {/* Stop Info Card */}
                <div style={{ padding: "14px 16px", borderRadius: "12px", background: "var(--bg-subtle)", border: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: "8px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "8px" }}>
                    <div>
                      <span style={{ fontSize: "11.5px", textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-3)", fontWeight: 600 }}>Pickup stop in roster</span>
                      <h4 style={{ margin: "2px 0 0", fontSize: "16px", fontWeight: 700, color: "var(--text)" }}>{verifyingStop.stop_name}</h4>
                    </div>
                    <span className="pill pill-accent">{verifyingStop.employees?.length || 1} {verifyingStop.employees?.length === 1 ? "rider" : "riders"}</span>
                  </div>

                  {verifyingStop.reason && (
                    <div style={{ fontSize: "12.5px", color: "var(--bad)", display: "flex", alignItems: "center", gap: "6px" }}>
                      <AlertTriangle size={14} /> {verifyingStop.reason}
                    </div>
                  )}

                  {/* List of affected employees */}
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "4px" }}>
                    {verifyingStop.employees?.map((emp, i) => (
                      <span key={i} className="pill" style={{ background: "var(--bg-card)", border: "1px solid var(--border)" }}>
                        <strong>{emp.employee_name}</strong> · Shift {emp.shift} {emp.mobile && emp.mobile !== "-" ? `· ${emp.mobile}` : ""}
                      </span>
                    ))}
                  </div>
                </div>

                {/* Live Search & Pinning Section */}
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <label style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)" }}>
                    Search location or paste Google Maps link / coordinates
                  </label>
                  <div className="dropoff-search-field">
                    <Search size={17} />
                    <input
                      type="text"
                      placeholder="Search landmark, chowk, or paste coordinates e.g. 18.7765, 73.6947"
                      value={verifySearchQuery}
                      onChange={(e) => setVerifySearchQuery(e.target.value)}
                      aria-label="Search location for pickup stop"
                    />
                    {isSearchingVerify && <span className="spinner" />}
                  </div>
                </div>

                {/* Candidate Search Results */}
                {verifyCandidates.length > 0 && (
                  <div className="dropoff-search-results" style={{ maxHeight: "200px" }}>
                    {verifyCandidates.map((place, idx) => (
                      <article className="dropoff-result-row" key={idx}>
                        <div className="dropoff-result-copy">
                          <strong>{place.name}</strong>
                          <span>{place.address}</span>
                          <span style={{ fontSize: "11px", color: "var(--text-3)", fontFamily: "monospace" }}>
                            {place.lat.toFixed(5)}, {place.lon.toFixed(5)}
                          </span>
                        </div>
                        <div className="dropoff-result-actions">
                          <a
                            className="dropoff-map-link"
                            href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(`${place.lat},${place.lon}`)}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <ExternalLink size={13} /> Map
                          </a>
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            onClick={() => {
                              setManualVerifyLat(String(place.lat));
                              setManualVerifyLon(String(place.lon));
                            }}
                          >
                            <Check size={13} /> Select
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                )}

                {/* Manual Latitude & Longitude Inputs */}
                <div style={{ padding: "14px 16px", borderRadius: "12px", background: "var(--bg-subtle)", border: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: "10px" }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--text)" }}>Confirmed coordinates</span>
                    {manualVerifyLat && manualVerifyLon && (
                      <a
                        href={`https://www.google.com/maps/search/?api=1&query=${manualVerifyLat},${manualVerifyLon}`}
                        target="_blank"
                        rel="noreferrer"
                        className="dropoff-map-link"
                      >
                        <ExternalLink size={13} /> View on Google Maps
                      </a>
                    )}
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px" }}>
                    <div>
                      <label style={{ display: "block", fontSize: "12px", color: "var(--text-2)", marginBottom: "4px" }}>Latitude</label>
                      <input
                        type="number"
                        step="any"
                        className="search-box"
                        style={{ width: "100%", height: "38px" }}
                        placeholder="e.g. 18.7350"
                        value={manualVerifyLat}
                        onChange={(e) => setManualVerifyLat(e.target.value)}
                      />
                    </div>
                    <div>
                      <label style={{ display: "block", fontSize: "12px", color: "var(--text-2)", marginBottom: "4px" }}>Longitude</label>
                      <input
                        type="number"
                        step="any"
                        className="search-box"
                        style={{ width: "100%", height: "38px" }}
                        placeholder="e.g. 73.6750"
                        value={manualVerifyLon}
                        onChange={(e) => setManualVerifyLon(e.target.value)}
                      />
                    </div>
                  </div>
                </div>

                {/* Status Message */}
                {verifyStatusMessage && (
                  <div
                    className={`dropoff-search-message ${verifyStatusMessage.startsWith("✅") ? "" : "is-error"}`}
                    style={{ padding: "12px 14px", textAlign: "left", alignItems: "flex-start" }}
                  >
                    {verifyStatusMessage}
                  </div>
                )}

                {/* Re-build routes prompt if any stops newly verified */}
                {newlyVerifiedStops.size > 0 && (
                  <div style={{ padding: "12px 16px", borderRadius: "10px", background: "var(--accent-tint)", border: "1px solid var(--accent)", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px" }}>
                    <span style={{ fontSize: "13px", color: "var(--accent-ink)", fontWeight: 500 }}>
                      {newlyVerifiedStops.size} {newlyVerifiedStops.size === 1 ? "stop" : "stops"} newly verified! Re-build routes to assign this employee to a bus route.
                    </span>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      onClick={() => { setShowVerifyModal(false); runOptimization(); }}
                    >
                      <RouteIcon size={14} /> Re-build routes
                    </button>
                  </div>
                )}
              </div>

              <div className="modal-footer" style={{ justifyContent: "space-between" }}>
                <div>
                  {unverifiedStopsList.length > 1 && (
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm"
                      onClick={goToNextUnverifiedStop}
                    >
                      Next unverified stop &rarr;
                    </button>
                  )}
                </div>
                <div style={{ display: "flex", gap: "8px" }}>
                  <button type="button" className="btn btn-secondary" onClick={() => setShowVerifyModal(false)}>
                    Close
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!manualVerifyLat || !manualVerifyLon || isSavingVerifyCoord}
                    onClick={handleSaveVerifiedCoordinate}
                  >
                    {isSavingVerifyCoord ? <span className="spinner" /> : <Check size={15} />} Save & Verify Stop
                  </button>
                </div>
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
              <div
                className={`kpi-detail ${clustering.unverified_employees ? "is-warning" : "is-good"}`}
                style={clustering.unverified_employees ? { cursor: "pointer", textDecoration: "underline" } : undefined}
                onClick={clustering.unverified_employees ? openVerifyModalForFirstUnverified : undefined}
                title={clustering.unverified_employees ? "Click to verify unmapped stops" : undefined}
              >
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
                {unverifiedCount > 0 && (
                  <button
                    type="button"
                    className="btn btn-warning btn-sm"
                    onClick={openVerifyModalForFirstUnverified}
                    style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                  >
                    <MapPin size={13} /> Verify {unverifiedCount} unresolved stops
                  </button>
                )}
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
                          {e.latitude && e.longitude && e.location_status === "VERIFIED" ? (
                            <div style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                              <button
                                className="btn btn-secondary btn-sm"
                                onClick={() => { setHoveredEmployee(e); setActiveTab("map"); }}
                              >
                                <Crosshair size={12} /> View on map
                              </button>
                              <button
                                type="button"
                                className="icon-btn"
                                title="Edit / re-verify pickup coordinates"
                                onClick={() => openVerifyModalForEmployee(e)}
                                style={{ width: "28px", height: "28px" }}
                              >
                                <MapPin size={13} />
                              </button>
                            </div>
                          ) : (
                            <div style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                              <button
                                type="button"
                                className="btn btn-warning btn-sm"
                                onClick={() => openVerifyModalForEmployee(e)}
                                title="Search and set verified coordinates for this pickup stop"
                              >
                                <MapPin size={13} /> Verify stop
                              </button>
                            </div>
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
