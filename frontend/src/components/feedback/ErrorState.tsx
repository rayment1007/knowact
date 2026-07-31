// ErrorState: a consistent "something went wrong" surface with retry.
//
// Rendered when a fetch or action fails (non-401 — 401s are handled globally by
// the API client → AuthProvider → ProtectedRoute redirect). Shows a message and
// an optional Retry button so recovery is one click away. Two layouts via
// `variant`:
//   - "inline" (default): a bordered row with the message and Retry side-by-side.
//   - "alert": a compact red alert box (for action failures within a form/panel).
// Purely presentational and prop-driven.

import type { ReactNode } from "react";

interface ErrorStateProps {
  /** The message to display. */
  message: ReactNode;
  /** When provided, a Retry button invokes this handler. */
  onRetry?: () => void;
  retryLabel?: string;
  variant?: "inline" | "alert";
  className?: string;
}

export default function ErrorState({
  message,
  onRetry,
  retryLabel = "Retry",
  variant = "inline",
  className = "",
}: ErrorStateProps) {
  if (variant === "alert") {
    return (
      <div
        role="alert"
        className={`flex items-start justify-between gap-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 ${className}`.trim()}
      >
        <p className="text-sm text-red-700">{message}</p>
        {onRetry ? (
          <button
            type="button"
            onClick={onRetry}
            className="shrink-0 rounded-md border border-red-300 bg-white px-2.5 py-1 text-xs font-medium text-red-700 transition hover:bg-red-100"
          >
            {retryLabel}
          </button>
        ) : null}
      </div>
    );
  }

  return (
    <div
      role="alert"
      className={`flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4 ${className}`.trim()}
    >
      <p className="text-sm text-red-600">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100"
        >
          {retryLabel}
        </button>
      ) : null}
    </div>
  );
}
