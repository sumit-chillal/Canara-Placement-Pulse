import { Component } from "react";

/**
 * Catches JS errors anywhere in the component tree below it and shows
 * a plain-language fallback instead of an unstyled blank page. Class
 * component because React error boundaries currently require the
 * componentDidCatch/getDerivedStateFromError lifecycle — there's no
 * hooks equivalent.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("Placement Pulse crashed:", error, info?.componentStack);
  }

  handleReload = () => {
    window.location.href = "/";
  };

  render() {
    if (!this.state.hasError) return this.props.children;

    return (
      <div className="pp-shell" data-testid="error-boundary">
        <div className="pp-container">
          <div className="pp-error-boundary">
            <h1 className="pp-title" style={{ fontSize: "2rem" }}>
              Something went wrong
            </h1>
            <p className="pp-lede">
              Placement Pulse hit an unexpected error. Your notices are
              still cached — reloading usually fixes this.
            </p>
            <button
              type="button"
              className="pp-btn"
              onClick={this.handleReload}
              data-testid="error-boundary-reload"
            >
              Reload
            </button>
          </div>
        </div>
      </div>
    );
  }
}