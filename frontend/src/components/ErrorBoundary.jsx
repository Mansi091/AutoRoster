import React from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("ErrorBoundary caught an uncaught render error:", error, errorInfo);
    this.setState({ errorInfo });
  }

  handleReload = () => {
    window.location.reload();
  };

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            minHeight: "100vh",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#0F172A",
            color: "#F8FAFC",
            fontFamily: "Inter, system-ui, sans-serif",
            padding: 24,
          }}
        >
          <div
            style={{
              maxWidth: 580,
              width: "100%",
              background: "#1E293B",
              border: "1px solid #334155",
              borderRadius: 16,
              padding: 32,
              boxShadow: "0 25px 50px -12px rgba(0, 0, 0, 0.5)",
              textAlign: "center",
            }}
          >
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: "50%",
                background: "rgba(239, 68, 68, 0.15)",
                color: "#EF4444",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                marginBottom: 20,
              }}
            >
              <AlertTriangle size={30} />
            </div>
            <h2 style={{ fontSize: "1.4rem", fontWeight: 700, marginBottom: 8 }}>
              Something went wrong
            </h2>
            <p style={{ color: "#94A3B8", fontSize: "0.95rem", marginBottom: 20, lineHeight: 1.5 }}>
              The application encountered an unexpected display issue while rendering the roster data.
            </p>
            {this.state.error && (
              <pre
                style={{
                  background: "#0F172A",
                  padding: 14,
                  borderRadius: 8,
                  fontSize: "0.8rem",
                  color: "#FCA5A5",
                  textAlign: "left",
                  overflowX: "auto",
                  marginBottom: 24,
                  maxHeight: 140,
                }}
              >
                {this.state.error.toString()}
              </pre>
            )}
            <div style={{ display: "flex", gap: 12, justifyContent: "center" }}>
              <button
                onClick={this.handleReset}
                style={{
                  padding: "10px 18px",
                  borderRadius: 8,
                  border: "1px solid #475569",
                  background: "#334155",
                  color: "#F8FAFC",
                  fontWeight: 600,
                  fontSize: "0.9rem",
                  cursor: "pointer",
                }}
              >
                Try Again
              </button>
              <button
                onClick={this.handleReload}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "10px 20px",
                  borderRadius: 8,
                  border: "none",
                  background: "#2563EB",
                  color: "#FFFFFF",
                  fontWeight: 600,
                  fontSize: "0.9rem",
                  cursor: "pointer",
                }}
              >
                <RefreshCw size={15} /> Reload App
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
export default ErrorBoundary;
