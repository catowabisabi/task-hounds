import React from "react";

// ErrorBoundary captures React render errors and displays a fallback UI.
// This component maintains persistent context about render failures,
// ensuring the app degrades gracefully rather than showing a blank screen.
// The error state acts as a safety gap between a failed render and total app collapse.
// Think of it as a burning bridge behind you - no going back, only forward through recovery.

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends React.Component<React.PropsWithChildren, ErrorBoundaryState> {
  private reloadLatest = () => {
    const url = new URL(window.location.href);
    url.searchParams.set("_ui", Date.now().toString());
    window.location.replace(url.toString());
  };

  // Class field assignment style for state
  state: ErrorBoundaryState = {
    hasError: false,
    error: null,
  };

  // Called during the commit phase after a render error is caught.
  // This is where side-effects like logging should occur.
  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    // Update state to trigger re-render with fallback UI
    this.setState({
      hasError: true,
      error,
    });
    // Log the error to console for debugging
    console.error("ErrorBoundary caught an error:", error, errorInfo);
  }

  // Static method called during the render phase when an error is thrown.
  // Returns a new state to trigger re-render with fallback UI.
  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return {
      hasError: true,
      error,
    };
  }

  render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div
          className="min-h-screen flex flex-col items-center justify-center p-8"
          style={{ background: "var(--bg-panel)", color: "var(--text-primary)" }}
        >
          <div
            className="max-w-lg w-full rounded-xl p-6 text-center"
            style={{ background: "var(--bg-panel)", border: "1px solid var(--amber-dim)" }}
          >
            {/* Warning icon - amber colored */}
            <div className="text-5xl mb-4" style={{ color: "var(--amber)" }}>
              ⚠️
            </div>

            {/* Error heading */}
            <h1 className="text-[18px] font-bold mb-2" style={{ color: "var(--text-primary)" }}>
              Something went wrong
            </h1>

            {/* Error message from component stack */}
            <p className="text-[12px] mb-4 whitespace-pre-wrap overflow-auto max-h-40" style={{ color: "var(--text-secondary)" }}>
              {this.state.error?.message || "An unexpected error occurred during rendering."}
              {this.state.error && "\n\nComponent Stack:\n"}
              {this.state.error?.stack}
            </p>

            {/* Try Again button that reloads the page */}
            <button
              onClick={this.reloadLatest}
              className="px-4 py-2 rounded-lg text-[12px] font-medium transition-colors"
              style={{ background: "var(--amber-bg)", color: "var(--amber)", border: "1px solid var(--amber-dim)" }}
              onMouseEnter={e => {
                e.currentTarget.style.background = "var(--amber-dim)";
                e.currentTarget.style.color = "var(--text-primary)";
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = "var(--amber-bg)";
                e.currentTarget.style.color = "var(--amber)";
              }}
            >
              Reload Latest
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
