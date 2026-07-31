// LoadingState: a consistent "content is loading" surface.
//
// Two presentations, driven by `variant`:
//   - "spinner" (default): a small animated spinner with an optional label,
//     for inline/compact areas (button-adjacent, small panels).
//   - "skeleton": a stack of card-shaped skeletons approximating a list, for
//     larger list/detail surfaces where a shape preview reads better.
//
// Purely presentational so every page can show the same loading affordance
// instead of ad-hoc "Loading…" text.

import { SkeletonCard } from "./Skeleton";

interface LoadingStateProps {
  variant?: "spinner" | "skeleton";
  /** Accessible / visible label for the spinner variant. */
  label?: string;
  /** Number of skeleton cards to render for the skeleton variant. */
  rows?: number;
  className?: string;
}

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin text-slate-400"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

export default function LoadingState({
  variant = "spinner",
  label = "Loading…",
  rows = 3,
  className = "",
}: LoadingStateProps) {
  if (variant === "skeleton") {
    return (
      <div
        role="status"
        aria-busy="true"
        aria-live="polite"
        className={`space-y-3 ${className}`.trim()}
      >
        {Array.from({ length: Math.max(1, rows) }).map((_, index) => (
          <SkeletonCard key={index} />
        ))}
        <span className="sr-only">{label}</span>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-busy="true"
      aria-live="polite"
      className={`flex items-center justify-center gap-2 py-8 text-sm text-slate-500 ${className}`.trim()}
    >
      <Spinner />
      <span>{label}</span>
    </div>
  );
}
