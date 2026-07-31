// EnterpriseDashboardPage: the landing page and daily brief surface
// (Requirements 10.1, 10.3, 12.3).
//
// On mount the page fetches the organization-wide daily brief from
// GET /api/brief/daily and renders its headline plus the four evidence-backed
// sections — priorities, recommended actions, follow-ups, and risks — each
// line grounded in its supporting evidence via the shared EvidenceBadge
// ("evidence everywhere"). The OperationalSupportDisclaimer frames every AI
// output as operational support only (Requirement 12.3). All data flows
// through the real Core Engine API (src/api/brief.ts); there is no hardcoded
// output.

import { useCallback, useEffect, useState } from "react";
import { briefApi, readableEvidence } from "@/api";
import type {
  ActionSuggestion,
  BriefLine,
  DailyBriefContent,
  FollowUpSuggestion,
} from "@/api";
import { useAuth } from "@/auth";
import EvidenceBadge from "@/components/EvidenceBadge";
import OperationalSupportDisclaimer from "@/components/OperationalSupportDisclaimer";
import { EmptyState, ErrorState, LoadingState } from "@/components/feedback";

/** Pick a greeting based on the local time of day. */
function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** Use the first name when available, otherwise fall back to the email. */
function firstName(fullName?: string, email?: string): string {
  if (fullName && fullName.trim()) {
    return fullName.trim().split(/\s+/)[0];
  }
  if (email) return email.split("@")[0];
  return "there";
}

/** Render an optional due-date hint, tolerating missing/invalid values. */
function formatDueHint(value?: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Section primitives
// ---------------------------------------------------------------------------

interface SectionProps {
  title: string;
  description: string;
  count: number;
  children: React.ReactNode;
}

/** A titled brief section that renders an empty note when it has no lines. */
function BriefSection({ title, description, count, children }: SectionProps) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <header className="mb-3">
        <h2 className="text-sm font-semibold text-slate-900">{title}</h2>
        <p className="mt-0.5 text-xs text-slate-500">{description}</p>
      </header>
      {count === 0 ? (
        <p className="rounded-md border border-dashed border-slate-200 bg-slate-50 px-3 py-4 text-center text-xs text-slate-400">
          Nothing here in today's brief.
        </p>
      ) : (
        children
      )}
    </section>
  );
}

/** A single evidence-backed narrative line (priority or risk). */
function BriefLineCard({ line }: { line: BriefLine }) {
  const evidence = readableEvidence(line);
  return (
    <li className="rounded-lg border border-slate-100 bg-slate-50/60 p-3">
      <p className="text-sm text-slate-800">{line.text}</p>
      {evidence ? (
        <EvidenceBadge text={evidence} label="Evidence" className="mt-2" />
      ) : null}
    </li>
  );
}

/** A recommended action with owner/due hints and its supporting evidence. */
function ActionCard({ action }: { action: ActionSuggestion }) {
  const due = formatDueHint(action.due_hint);
  return (
    <li className="rounded-lg border border-slate-100 bg-slate-50/60 p-3">
      <p className="text-sm font-medium text-slate-900">{action.title}</p>
      {action.description ? (
        <p className="mt-0.5 text-sm text-slate-600">{action.description}</p>
      ) : null}
      {action.owner_hint || due ? (
        <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
          {action.owner_hint ? (
            <span>Owner: {action.owner_hint}</span>
          ) : null}
          {due ? <span>Due: {due}</span> : null}
        </div>
      ) : null}
      <EvidenceBadge text={action.evidence_text} label="Evidence" className="mt-2" />
    </li>
  );
}

/** A follow-up suggestion with an optional due hint and evidence. */
function FollowUpCard({ followUp }: { followUp: FollowUpSuggestion }) {
  const due = formatDueHint(followUp.due_hint);
  return (
    <li className="rounded-lg border border-slate-100 bg-slate-50/60 p-3">
      <p className="text-sm text-slate-800">{followUp.text}</p>
      {due ? (
        <div className="mt-1 text-xs text-slate-500">Due: {due}</div>
      ) : null}
      {followUp.evidence_text ? (
        <EvidenceBadge
          text={followUp.evidence_text}
          label="Evidence"
          className="mt-2"
        />
      ) : null}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function EnterpriseDashboardPage() {
  const { user } = useAuth();

  const [brief, setBrief] = useState<DailyBriefContent | null>(null);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadBrief = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await briefApi.daily();
      setBrief(result.content);
      setGeneratedAt(result.created_at);
    } catch {
      setError("Could not generate your daily brief. Please retry.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadBrief();
  }, [loadBrief]);

  const generatedLabel = (() => {
    if (!generatedAt) return null;
    const date = new Date(generatedAt);
    if (Number.isNaN(date.getTime())) return null;
    return date.toLocaleString();
  })();

  return (
    <div className="mx-auto max-w-6xl px-6 py-8">
      {/* Greeting */}
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">
          {greeting()}, {firstName(user?.full_name, user?.email)}
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Your daily brief, drawn from confirmed organizational context.
        </p>
      </header>

      {/* Operational-support guardrail (Requirement 12.3) */}
      <OperationalSupportDisclaimer className="mb-6" />

      {/* Daily brief (Requirements 10.1, 10.3) */}
      {loading ? (
        <LoadingState variant="skeleton" rows={4} label="Generating your daily brief…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadBrief()} />
      ) : !brief ? (
        <EmptyState title="No daily brief is available yet." />
      ) : (
        <div className="space-y-6">
          {/* Headline */}
          <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-brand-600">
                  AI Daily Brief
                </p>
                <h2 className="mt-1 text-lg font-semibold text-slate-900">
                  {brief.headline}
                </h2>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                {generatedLabel ? (
                  <span className="text-xs text-slate-400">
                    Generated {generatedLabel}
                  </span>
                ) : null}
                <button
                  type="button"
                  onClick={() => void loadBrief()}
                  className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100"
                >
                  Regenerate
                </button>
              </div>
            </div>
          </section>

          {/* Four evidence-backed sections (Requirement 10.3) */}
          <div className="grid gap-6 lg:grid-cols-2">
            <BriefSection
              title="Priorities"
              description="What needs your attention first today."
              count={brief.priorities.length}
            >
              <ul className="space-y-2">
                {brief.priorities.map((line, index) => (
                  <BriefLineCard key={`priority-${index}`} line={line} />
                ))}
              </ul>
            </BriefSection>

            <BriefSection
              title="Recommended actions"
              description="Suggested next steps drawn from open work."
              count={brief.recommended_actions.length}
            >
              <ul className="space-y-2">
                {brief.recommended_actions.map((action, index) => (
                  <ActionCard key={`action-${index}`} action={action} />
                ))}
              </ul>
            </BriefSection>

            <BriefSection
              title="Follow-ups"
              description="Things to circle back on."
              count={brief.follow_ups.length}
            >
              <ul className="space-y-2">
                {brief.follow_ups.map((followUp, index) => (
                  <FollowUpCard key={`follow-up-${index}`} followUp={followUp} />
                ))}
              </ul>
            </BriefSection>

            <BriefSection
              title="Risks"
              description="Potential issues surfaced from your context."
              count={brief.risks.length}
            >
              <ul className="space-y-2">
                {brief.risks.map((line, index) => (
                  <BriefLineCard key={`risk-${index}`} line={line} />
                ))}
              </ul>
            </BriefSection>
          </div>
        </div>
      )}
    </div>
  );
}
