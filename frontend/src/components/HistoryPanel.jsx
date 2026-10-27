import React, { useState, useEffect } from "react";
import { Download, Calendar, FileText, MapPin, Users, Bus, RefreshCw, AlertTriangle } from "lucide-react";
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
      <div className="table-filter-bar">
        <div className="table-title">
          <strong>Your roster history</strong>
          <span>{historyList.length} saved {historyList.length === 1 ? "plan" : "plans"}</span>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={loadData} disabled={isLoading}>
          <RefreshCw size={14} className={isLoading ? "spin" : ""} /> Refresh
        </button>
      </div>

      {errorMessage && (
        <div className="banner banner-error" role="alert" style={{ margin: 16 }}>
          <AlertTriangle size={16} />
          <div>{errorMessage}</div>
        </div>
      )}

      <div className="table-scroll">
        {isLoading && historyList.length === 0 ? (
          <table className="custom-table">
            <tbody>
              {[0, 1, 2, 3].map((i) => (
                <tr key={i} className="skeleton-row">
                  {[0, 1, 2, 3, 4, 5].map((j) => (
                    <td key={j}><span className="skeleton" /></td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        ) : historyList.length === 0 ? (
          <div className="empty-state">
            <span className="empty-icon"><FileText size={24} /></span>
            <h4>No previous rosters yet</h4>
            <p>When you run an optimization it is automatically archived here, so you can download it again later.</p>
          </div>
        ) : (
          <table className="custom-table">
            <thead>
              <tr>
                <th>Run date</th>
                <th>Uploaded sheet</th>
                <th>Drop-off plant</th>
                <th>Employees</th>
                <th>Routes</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {historyList.map((item, idx) => (
                <tr key={item.id || idx}>
                  <td className="cell-strong" style={{ whiteSpace: "nowrap" }}>
                    <span className="cell-inline"><Calendar size={14} color="var(--accent)" /> {formatDate(item.created_at)}</span>
                  </td>
                  <td className="cell-strong">{item.uploaded_file_name || "Uploaded roster"}</td>
                  <td>
                    <span className="cell-inline cell-muted"><MapPin size={14} color="var(--bad)" /> {item.destination_name || "Not recorded"}</span>
                  </td>
                  <td><span className="pill pill-accent"><Users size={12} /> {item.routed_employees ?? item.total_employees ?? 0} pax</span></td>
                  <td><span className="pill pill-teal"><Bus size={12} /> {item.total_routes} routes</span></td>
                  <td>
                    <button
                      type="button"
                      onClick={() => downloadRoster(item.workbook_filename)}
                      className="btn btn-secondary btn-sm"
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
