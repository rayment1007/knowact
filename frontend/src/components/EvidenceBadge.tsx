// EvidenceBadge: a compact, evidence-first display used across the app.
//
// "Evidence everywhere" is a core design principle — every classification,
// knowledge item, action, and brief line references the source text that
// produced it. This component renders that supporting text compactly so it can
// be reused by the Source Inbox, Knowledge Hub, and later pages. It is generic
// and prop-driven: pass either a single `text` string or a list of `spans`.

export interface EvidenceItem {
  text: string;
  start?: number;
  end?: number;
}

interface EvidenceBadgeProps {
  /** A single piece of evidence text (e.g. a knowledge item's evidence_text). */
  text?: string;
  /** Multiple evidence spans (e.g. a classification's evidence_spans). */
  spans?: EvidenceItem[];
  /** Optional label shown before the evidence. Defaults to "Evidence". */
  label?: string;
  className?: string;
}

/** Filter out blank spans so we never render empty chips. */
function nonEmptySpans(spans: EvidenceItem[] | undefined): EvidenceItem[] {
  if (!spans) return [];
  return spans.filter((span) => span.text != null && span.text.trim() !== "");
}

export default function EvidenceBadge({
  text,
  spans,
  label = "Evidence",
  className = "",
}: EvidenceBadgeProps) {
  const items = nonEmptySpans(spans);
  const hasText = text != null && text.trim() !== "";

  if (!hasText && items.length === 0) {
    return null;
  }

  return (
    <div className={`text-xs text-slate-500 ${className}`.trim()}>
      <span className="font-medium uppercase tracking-wide text-slate-400">
        {label}
      </span>
      {hasText ? (
        <blockquote className="mt-1 border-l-2 border-slate-200 pl-2 italic text-slate-600">
          “{text}”
        </blockquote>
      ) : null}
      {items.length > 0 ? (
        <div className="mt-1 flex flex-wrap gap-1">
          {items.map((span, index) => (
            <span
              key={`${span.text}-${index}`}
              className="inline-block max-w-full truncate rounded bg-slate-100 px-1.5 py-0.5 text-slate-600"
              title={span.text}
            >
              “{span.text}”
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
