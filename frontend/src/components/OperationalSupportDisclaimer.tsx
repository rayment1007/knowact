// OperationalSupportDisclaimer: the regulated-advice guardrail notice.
//
// Requirement 12.3 requires that AI output is always framed as operational
// support only — never financial, legal, tax, insurance, medical, or
// investment advice. This shared component is rendered in the AppShell (so it
// is present on every authenticated page) and can also be embedded inline on
// every page that surfaces AI output.

interface OperationalSupportDisclaimerProps {
  /**
   * "banner" renders a full-width bordered notice (used inline on pages).
   * "footnote" renders a compact single-line variant (used in the shell chrome).
   */
  variant?: "banner" | "footnote";
  className?: string;
}

const MESSAGE =
  "AI-generated outputs are operational support only and do not constitute financial, legal, tax, insurance, medical, or investment advice.";

export default function OperationalSupportDisclaimer({
  variant = "banner",
  className = "",
}: OperationalSupportDisclaimerProps) {
  if (variant === "footnote") {
    return (
      <p
        role="note"
        className={`text-xs text-slate-400 ${className}`.trim()}
      >
        {MESSAGE}
      </p>
    );
  }

  return (
    <div
      role="note"
      className={`rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 ${className}`.trim()}
    >
      <span className="font-medium">Operational support only.</span> {MESSAGE}
    </div>
  );
}
