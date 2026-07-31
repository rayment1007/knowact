// SuggestionCard: a generic presentation of a human-in-the-loop AI suggestion.
//
// Every AI output in the platform is a *suggestion* a human must explicitly
// confirm or reject before it mutates business memory. This card renders that
// pattern consistently — a headline, an optional confidence meter, evidence,
// evidence-backed reasons, and confirm/reject actions — so it can be reused by
// the Source Inbox (classification suggestions) and later pages (knowledge,
// meeting summaries). It is fully prop-driven and owns no data-fetching logic.

import type { ReactNode } from "react";
import type { SuggestionStatus } from "@/api";
import EvidenceBadge from "@/components/EvidenceBadge";
import type { EvidenceItem } from "@/components/EvidenceBadge";

interface SuggestionCardProps {
  /** Short headline, e.g. the suggested category. */
  title: ReactNode;
  /** Optional secondary line under the title. */
  subtitle?: ReactNode;
  /** Confidence in [0, 1]; rendered as a percentage meter when provided. */
  confidence?: number;
  /** Evidence-backed reasons for the suggestion (Requirement 4.4). */
  reasons?: string[];
  /** Evidence text / spans supporting the suggestion. */
  evidenceText?: string;
  evidenceSpans?: EvidenceItem[];
  /** Current human-in-the-loop status; drives the status pill. */
  status?: SuggestionStatus;
  /** Confirm / reject handlers. When omitted, the action is not shown. */
  onConfirm?: () => void;
  onReject?: () => void;
  confirmLabel?: string;
  rejectLabel?: string;
  /** Disables both action buttons (e.g. while a request is in flight). */
  busy?: boolean;
  /** Extra content rendered above the actions (e.g. override selectors). */
  children?: ReactNode;
  className?: string;
}

const STATUS_STYLES: Record<SuggestionStatus, string> = {
  SUGGESTED: "bg-amber-50 text-amber-700 border-amber-200",
  CONFIRMED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  REJECTED: "bg-rose-50 text-rose-700 border-rose-200",
};

function clampPercent(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, Math.round(value * 100)));
}

export default function SuggestionCard({
  title,
  subtitle,
  confidence,
  reasons,
  evidenceText,
  evidenceSpans,
  status,
  onConfirm,
  onReject,
  confirmLabel = "Confirm",
  rejectLabel = "Reject",
  busy = false,
  children,
  className = "",
}: SuggestionCardProps) {
  const percent =
    confidence === undefined ? undefined : clampPercent(confidence);
  const cleanReasons = (reasons ?? []).filter(
    (reason) => reason != null && reason.trim() !== "",
  );
  const showActions = Boolean(onConfirm || onReject);

  return (
    <div
      className={`rounded-lg border border-slate-200 bg-white p-4 shadow-sm ${className}`.trim()}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="rounded bg-brand-50 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-brand-700">
              AI suggestion
            </span>
            {status ? (
              <span
                className={`rounded border px-2 py-0.5 text-xs font-medium ${STATUS_STYLES[status]}`}
              >
                {status}
              </span>
            ) : null}
          </div>
          <h3 className="mt-2 truncate text-base font-semibold text-slate-900">
            {title}
          </h3>
          {subtitle ? (
            <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>
          ) : null}
        </div>

        {percent !== undefined ? (
          <div className="w-28 shrink-0 text-right">
            <div className="text-xs font-medium text-slate-500">
              Confidence
            </div>
            <div className="mt-1 text-sm font-semibold text-slate-900">
              {percent}%
            </div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-brand-500"
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
        ) : null}
      </div>

      {cleanReasons.length > 0 ? (
        <ul className="mt-3 list-inside list-disc space-y-1 text-sm text-slate-600">
          {cleanReasons.map((reason, index) => (
            <li key={`${reason}-${index}`}>{reason}</li>
          ))}
        </ul>
      ) : null}

      {evidenceText || (evidenceSpans && evidenceSpans.length > 0) ? (
        <EvidenceBadge
          text={evidenceText}
          spans={evidenceSpans}
          className="mt-3"
        />
      ) : null}

      {children ? <div className="mt-3">{children}</div> : null}

      {showActions ? (
        <div className="mt-4 flex items-center gap-2">
          {onConfirm ? (
            <button
              type="button"
              onClick={onConfirm}
              disabled={busy}
              className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-700 focus:outline-none focus:ring-2 focus:ring-brand-500 focus:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {confirmLabel}
            </button>
          ) : null}
          {onReject ? (
            <button
              type="button"
              onClick={onReject}
              disabled={busy}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-300 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {rejectLabel}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
