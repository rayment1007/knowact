// EmptyState: a consistent "nothing here yet" surface.
//
// Rendered when a successful fetch returns no rows, or a filtered view has no
// matches. A dashed bordered panel with a short message and optional action so
// empty surfaces feel intentional rather than broken. Purely presentational.

import type { ReactNode } from "react";

interface EmptyStateProps {
  /** Primary message, e.g. "No source items yet." */
  title: ReactNode;
  /** Optional secondary line offering guidance. */
  description?: ReactNode;
  /** Optional call-to-action (e.g. a button) rendered under the message. */
  action?: ReactNode;
  /** "panel" (default) draws a dashed card; "plain" is text-only for tight areas. */
  variant?: "panel" | "plain";
  className?: string;
}

export default function EmptyState({
  title,
  description,
  action,
  variant = "panel",
  className = "",
}: EmptyStateProps) {
  if (variant === "plain") {
    return (
      <div className={`py-6 text-center text-sm text-slate-500 ${className}`.trim()}>
        <p>{title}</p>
        {description ? (
          <p className="mt-1 text-xs text-slate-400">{description}</p>
        ) : null}
        {action ? <div className="mt-3">{action}</div> : null}
      </div>
    );
  }

  return (
    <div
      className={`rounded-xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center ${className}`.trim()}
    >
      <p className="text-sm text-slate-600">{title}</p>
      {description ? (
        <p className="mx-auto mt-1 max-w-md text-xs text-slate-400">
          {description}
        </p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
