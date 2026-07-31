// Skeleton: a neutral, animated placeholder block used while content loads.
//
// Skeletons communicate "content is coming" with a shape that roughly matches
// the eventual layout, which reads better than a bare spinner for list/detail
// surfaces. Purely presentational and prop-driven.

interface SkeletonProps {
  /** Extra classes to size/shape the block (height, width, rounding). */
  className?: string;
}

export default function Skeleton({ className = "" }: SkeletonProps) {
  return (
    <div
      aria-hidden
      className={`animate-pulse rounded-md bg-slate-200/70 ${className}`.trim()}
    />
  );
}

interface SkeletonTextProps {
  /** Number of lines to render. Defaults to 3. */
  lines?: number;
  className?: string;
}

/** A stack of skeleton lines approximating a paragraph of text. */
export function SkeletonText({ lines = 3, className = "" }: SkeletonTextProps) {
  return (
    <div className={`space-y-2 ${className}`.trim()}>
      {Array.from({ length: Math.max(1, lines) }).map((_, index) => (
        <Skeleton
          key={index}
          className={`h-3.5 ${index === lines - 1 ? "w-2/3" : "w-full"}`}
        />
      ))}
    </div>
  );
}

interface SkeletonCardProps {
  className?: string;
}

/** A card-shaped skeleton matching the app's bordered list/detail cards. */
export function SkeletonCard({ className = "" }: SkeletonCardProps) {
  return (
    <div
      className={`rounded-xl border border-slate-200 bg-white p-4 shadow-sm ${className}`.trim()}
    >
      <div className="flex items-center gap-3">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-5 w-16" />
      </div>
      <SkeletonText lines={2} className="mt-3" />
    </div>
  );
}
