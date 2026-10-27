import React, { useState, useEffect } from "react";
import { Download, Calendar, FileText, MapPin, Users, Bus, RefreshCw } from "lucide-react";
import { fetchRosterHistory, authenticatedFetch } from "../utils/clerk";

export default function HistoryPanel({ currentUser, getToken }) {
  const [historyList, setHistoryList] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  const loadData = async () => {
    setIsLoading(true);
    try {
      const rosters = await fetchRosterHistory(getToken);
      setHistoryList(rosters || []);
      setErrorMessage("");
    } catch (err) {
      console.warn("Error loading history:", err);
      setErrorMessage(err.message || "Could not load your roster history.");
    } finally {
      setIsLoading(false);
    }
  };

  const downloadRoster = async (filename) => {
    try {
      const response = await authenticatedFetch(`/api/history/download/${encodeURIComponent(filename)}`, {}, getToken);
      if (!response.ok) throw new Error("Could not download this roster.");
      const objectUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      setErrorMessage(err.message || "Could not download this roster.");
    }
  };

  useEffect(() => {
    loadData();
  }, [currentUser?.id, getToken]);

  const formatDate = (isoStr) => {
    if (!isoStr) return "-";
    try {
      const d = new Date(isoStr);
      return d.toLocaleDateString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch {
      return isoStr;
    }
  };

  return (
    <div className="table-panel">
      {/* This view is scoped to the authenticated supervisor by the backend. */}
      <div className="table-filter-bar" style={{ justifyContent: "space-between" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <strong style={{ color: "#17243b", fontSize: "1rem" }}>Your roster history</strong>
          <span style={{ color: "#64748b", fontSize: ".78rem" }}>{historyList.length} saved {historyList.length === 1 ? "plan" : "plans"}</span>
        </div>

        <button
          className="btn btn-secondary btn-sm"
          onClick={loadData}
          disabled={isLoading}
          style={{ display: "flex", alignItems: "center", gap: 6 }}
        >
          <RefreshCw size={14} className={isLoading ? "spin" : ""} /> Refresh
        </button>
      </div>

      {errorMessage && <div className="route-error-banner" role="alert">{errorMessage}</div>}
      <div className="table-scroll">
          {historyList.length === 0 ? (
            <div style={{ padding: "60px 20px", textAlign: "center", color: "#64748B" }}>
              <FileText size={42} style={{ margin: "0 auto 12px", opacity: 0.4 }} />
              <h4>No Previous Rosters Yet</h4>
              <p style={{ fontSize: "0.85rem", marginTop: 4 }}>
                When you run an optimization or upload an Excel sheet, it will be automatically archived here.
              </p>
            </div>
          ) : (
            <table className="custom-table">
              <thead>
                <tr>
                  <th>Run Date</th>
                  <th>Uploaded Sheet</th>
                  <th>Drop-off Plant</th>
                  <th>Employees</th>
                  <th>Routes</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {historyList.map((item, idx) => (
                  <tr key={item.id || idx}>
                    <td style={{ fontWeight: 600, color: "#1E293B", whiteSpace: "nowrap" }}>
                      <Calendar size={13} style={{ display: "inline", marginRight: 5, color: "#2563EB" }} />
                      {formatDate(item.created_at)}
                    </td>
                    <td style={{ fontWeight: 600 }}>{item.uploaded_file_name || "Uploaded roster"}</td>
                    <td>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "#475569" }}>
                        <MapPin size={13} color="#DC2626" />
                        {item.destination_name || "Not recorded"}
                      </span>
                    </td>
                    <td>
                      <span className="badge-seats" style={{ background: "#EFF6FF", color: "#1D4ED8" }}>
                        <Users size={12} style={{ display: "inline", marginRight: 3 }} />
                        {item.routed_employees ?? item.total_employees ?? 0} pax
                      </span>
                    </td>
                    <td>
                      <span className="badge-seats" style={{ background: "#F0FDF4", color: "#15803D" }}>
                        <Bus size={12} style={{ display: "inline", marginRight: 3 }} />
                        {item.total_routes} routes
                      </span>
                    </td>
                    <td>
                      <button
                        type="button"
                        onClick={() => downloadRoster(item.workbook_filename)}
                        className="btn btn-secondary btn-sm"
                        style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "4px 10px" }}
                        title="Download historical Excel workbook"
                      >
                        <Download size={13} /> Download Excel
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </div>
    </div>
  );
}
