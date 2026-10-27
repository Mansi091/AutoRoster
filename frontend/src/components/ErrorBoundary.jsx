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
        <div className="error-screen">
          <div className="card error-card">
            <span className="error-icon"><AlertTriangle size={28} /></span>
            <h2>Something went wrong</h2>
            <p>The application hit an unexpected display issue while rendering the roster data. Your saved history is not affected.</p>
            {this.state.error && (
              <pre className="error-detail">{String(this.state.error?.message || this.state.error)}</pre>
            )}
            <div className="error-actions">
              <button type="button" className="btn btn-secondary" onClick={this.handleReset}>Try again</button>
              <button type="button" className="btn btn-primary" onClick={this.handleReload}>
                <RefreshCw size={15} /> Reload app
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
