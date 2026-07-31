// SafetyRefusal: a consistent surface for a backend safety refusal (409).
//
// The Core Engine refuses to extract knowledge in two cases (Requirement 6.4):
//   - Privacy gate: the item's category/relevance (PERSONAL, SPAM, IRRELEVANT,
//     SYSTEM_NOTIFICATION) forbids extraction outright — a non-blocking notice
//     with no path forward (`variant="blocked"`).
//   - Sensitivity gate: HIGHLY_SENSITIVE content is blocked until the user
//     *explicitly acknowledges* handling it, after which extraction is retried
//     with `acknowledged=true` (`variant="acknowledge"`). This is the blocking
//     acknowledgment path.
//
// Centralizing both presentations keeps the 409 safety-refusal explanation and
// the sensitivity acknowledgment flow consistent wherever extraction happens.

interface SafetyRefusalProps {
  /**
   * "acknowledge": the highly-sensitive blocking gate with Acknowledge/Cancel
   * actions. "blocked": a non-blocking refusal with no path forward.
   */
  variant: "acknowledge" | "blocked";
  /** The refusal explanation to show. */
  message: string;
  /** Acknowledge handler (retries extraction with acknowledged=true). */
  onAcknowledge?: () => void;
  /** Cancel handler (dismisses the acknowledgment prompt). */
  onCancel?: () => void;
  /** Disables the action buttons while a request is in flight. */
  busy?: boolean;
  acknowledgeLabel?: string;
  className?: string;
}

export default function SafetyRefusal({
  variant,
  message,
  onAcknowledge,
  onCancel,
  busy = false,
  acknowledgeLabel = "I acknowledge — extract anyway",
  className = "",
}: SafetyRefusalProps) {
  if (variant === "blocked") {
    return (
      <div
        role="alert"
        className={`rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 ${className}`.trim()}
      >
        {message}
      </div>
    );
  }

  return (
    <div
      role="alert"
      className={`rounded-md border border-rose-300 bg-rose-50 px-4 py-3 ${className}`.trim()}
    >
      <div className="flex items-center gap-2">
        <span className="rounded bg-rose-600 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-white">
          Highly sensitive
        </span>
        <span className="text-sm font-semibold text-rose-800">
          Acknowledgment required
        </span>
      </div>
      <p className="mt-2 text-sm text-rose-700">{message}</p>
      <div className="mt-3 flex items-center gap-2">
        {onAcknowledge ? (
          <button
            type="button"
            onClick={onAcknowledge}
            disabled={busy}
            className="rounded-md bg-rose-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-rose-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? "Extracting…" : acknowledgeLabel}
          </button>
        ) : null}
        {onCancel ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Cancel
          </button>
        ) : null}
      </div>
    </div>
  );
}
