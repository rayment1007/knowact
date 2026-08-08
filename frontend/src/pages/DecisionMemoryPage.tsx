// DecisionMemoryPage: the "why we chose this" surface of business memory
// (Requirement 9).
//
// Responsibilities:
//   - Record a decision together with its rationale and supporting evidence
//     (Requirement 9.1) via a form; the created record is immutable.
//   - List decision records with their rationale and evidence, filterable by
//     business entity (Requirement 9.3).
//
// Evidence is always shown via the shared EvidenceBadge ("evidence
// everywhere"). All data flows through the real Core Engine API
// (src/api/decisions.ts, src/api/businessEntities.ts); there is no hardcoded
// output.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  businessEntitiesApi,
  decisionsApi,
  getErrorMessage,
  parseFieldErrors,
} from "@/api";
import type {
  BusinessEntity,
  DecisionCreate,
  DecisionRecord,
  FieldErrors,
} from "@/api";
import EvidenceBadge from "@/components/EvidenceBadge";
import { EmptyState, ErrorState, FieldError, LoadingState } from "@/components/feedback";

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// Record-decision form (Requirement 9.1)
// ---------------------------------------------------------------------------

interface DecisionFormProps {
  entities: BusinessEntity[];
  onCreated: (decision: DecisionRecord) => void;
}

function DecisionForm({ entities, onCreated }: DecisionFormProps) {
  const [title, setTitle] = useState("");
  const [decision, setDecision] = useState("");
  const [rationale, setRationale] = useState("");
  const [evidenceText, setEvidenceText] = useState("");
  const [entityId, setEntityId] = useState("");
  const [decidedAt, setDecidedAt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  function reset() {
    setTitle("");
    setDecision("");
    setRationale("");
    setEvidenceText("");
    setEntityId("");
    setDecidedAt("");
    setError(null);
    setFieldErrors({});
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    if (
      !title.trim() ||
      !decision.trim() ||
      !rationale.trim() ||
      !evidenceText.trim()
    ) {
      setError("Title, decision, rationale, and evidence are all required.");
      setFieldErrors({
        ...(title.trim() ? {} : { title: "Required." }),
        ...(decision.trim() ? {} : { decision: "Required." }),
        ...(rationale.trim() ? {} : { rationale: "Required." }),
        ...(evidenceText.trim() ? {} : { evidence_text: "Required." }),
      });
      return;
    }
    setSubmitting(true);
    setError(null);
    setFieldErrors({});
    const payload: DecisionCreate = {
      title: title.trim(),
      decision: decision.trim(),
      rationale: rationale.trim(),
      evidence_text: evidenceText.trim(),
      business_entity_id: entityId || undefined,
      decided_at: decidedAt || undefined,
    };
    try {
      const created = await decisionsApi.create(payload);
      onCreated(created);
      reset();
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setFieldErrors(parseFieldErrors(err));
      }
      setError(getErrorMessage(err, "Could not record the decision. Please retry."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-3 rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
    >
      <h2 className="text-sm font-semibold text-slate-900">Record a decision</h2>

      <div>
        <label
          htmlFor="decision-title"
          className="block text-xs font-medium text-slate-600"
        >
          Title
        </label>
        <input
          id="decision-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          aria-invalid={Boolean(fieldErrors.title)}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          placeholder="Short name for this decision"
        />
        <FieldError message={fieldErrors.title} />
      </div>

      <div>
        <label
          htmlFor="decision-decision"
          className="block text-xs font-medium text-slate-600"
        >
          Decision
        </label>
        <textarea
          id="decision-decision"
          value={decision}
          onChange={(e) => setDecision(e.target.value)}
          rows={2}
          aria-invalid={Boolean(fieldErrors.decision)}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          placeholder="What was decided?"
        />
        <FieldError message={fieldErrors.decision} />
      </div>

      <div>
        <label
          htmlFor="decision-rationale"
          className="block text-xs font-medium text-slate-600"
        >
          Rationale
        </label>
        <textarea
          id="decision-rationale"
          value={rationale}
          onChange={(e) => setRationale(e.target.value)}
          rows={3}
          aria-invalid={Boolean(fieldErrors.rationale)}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          placeholder="Why was this decision made?"
        />
        <FieldError message={fieldErrors.rationale} />
      </div>

      <div>
        <label
          htmlFor="decision-evidence"
          className="block text-xs font-medium text-slate-600"
        >
          Evidence
        </label>
        <textarea
          id="decision-evidence"
          value={evidenceText}
          onChange={(e) => setEvidenceText(e.target.value)}
          rows={2}
          aria-invalid={Boolean(fieldErrors.evidence_text)}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          placeholder="Source text supporting this decision"
        />
        <FieldError message={fieldErrors.evidence_text} />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label
            htmlFor="decision-entity"
            className="block text-xs font-medium text-slate-600"
          >
            Business entity
          </label>
          <select
            id="decision-entity"
            value={entityId}
            onChange={(e) => setEntityId(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="">Unlinked</option>
            {entities.map((entity) => (
              <option key={entity.id} value={entity.id}>
                {entity.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label
            htmlFor="decision-date"
            className="block text-xs font-medium text-slate-600"
          >
            Decided at
          </label>
          <input
            id="decision-date"
            type="date"
            value={decidedAt}
            onChange={(e) => setDecidedAt(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        </div>
      </div>

      {error ? <ErrorState message={error} variant="alert" /> : null}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? "Recording…" : "Record decision"}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Decision card
// ---------------------------------------------------------------------------

interface DecisionCardProps {
  decision: DecisionRecord;
  entityName: string | null;
  busy: boolean;
  onDelete: () => void;
}

function DecisionCard({ decision, entityName, busy, onDelete }: DecisionCardProps) {
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link
          to={`/decisions/${decision.id}`}
          className="text-sm font-semibold text-slate-900 hover:text-brand-700"
        >
          {decision.title}
        </Link>
        <span className="text-xs text-slate-400">
          Decided {formatDate(decision.decided_at)}
        </span>
      </div>
      <p className="mt-1 text-sm text-slate-700">{decision.decision}</p>
      <div className="mt-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          Rationale
        </div>
        <p className="mt-0.5 text-sm text-slate-600">{decision.rationale}</p>
      </div>
      <div className="mt-2 text-xs text-slate-400">
        {entityName ?? "Unlinked entity"}
      </div>
      <EvidenceBadge text={decision.evidence_text} className="mt-2" />
      <div className="mt-3 flex justify-end">
        <button
          type="button"
          onClick={onDelete}
          disabled={busy}
          className="text-xs font-medium text-red-500 underline-offset-2 transition hover:text-red-700 hover:underline disabled:cursor-not-allowed disabled:opacity-60"
        >
          Delete permanently
        </button>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DecisionMemoryPage() {
  const { decisionId } = useParams<{ decisionId: string }>();
  const navigate = useNavigate();
  const [decisions, setDecisions] = useState<DecisionRecord[]>([]);
  const [entities, setEntities] = useState<BusinessEntity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openedDecision, setOpenedDecision] =
    useState<DecisionRecord | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [entityFilter, setEntityFilter] = useState<string>("");

  const filters = useMemo(
    () => ({ businessEntityId: entityFilter || undefined }),
    [entityFilter],
  );

  const loadOpenedDecision = useCallback(async () => {
    if (!decisionId) {
      setOpenedDecision(null);
      setDetailError(null);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    try {
      setOpenedDecision(await decisionsApi.get(decisionId));
    } catch (err) {
      setOpenedDecision(null);
      setDetailError(
        err instanceof ApiError && err.status === 404
          ? "This decision could not be found."
          : "Could not load the opened decision. Please retry.",
      );
    } finally {
      setDetailLoading(false);
    }
  }, [decisionId]);

  useEffect(() => {
    void loadOpenedDecision();
  }, [loadOpenedDecision]);

  // Business entities power the filter dropdown and the form; a load failure
  // here should not block the decision list, so it is best-effort.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await businessEntitiesApi.list();
        if (!cancelled) setEntities(result);
      } catch {
        if (!cancelled) setEntities([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const entityNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const entity of entities) map.set(entity.id, entity.name);
    return map;
  }, [entities]);

  const loadDecisions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await decisionsApi.list(filters);
      setDecisions(result);
    } catch {
      setError("Could not load decisions. Please retry.");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void loadDecisions();
  }, [loadDecisions]);

  const handleCreated = useCallback((created: DecisionRecord) => {
    setDecisions((current) => [created, ...current]);
  }, []);

  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const handleDelete = useCallback(
    async (decision: DecisionRecord) => {
      if (deletingId) return;
      const confirmed = window.confirm(
        `Permanently delete the decision "${decision.title}"? This cannot be undone.`,
      );
      if (!confirmed) return;
      setDeletingId(decision.id);
      setDeleteError(null);
      try {
        await decisionsApi.remove(decision.id);
        setDecisions((current) =>
          current.filter((it) => it.id !== decision.id),
        );
        if (openedDecision?.id === decision.id) {
          setOpenedDecision(null);
          navigate("/decisions", { replace: true });
        }
      } catch (err) {
        setDeleteError(
          getErrorMessage(err, "Could not delete the decision. Please retry."),
        );
      } finally {
        setDeletingId(null);
      }
    },
    [deletingId, navigate, openedDecision?.id],
  );

  return (
    <section className="mx-auto max-w-5xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">
          Decision Memory
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Preserve the reasoning behind past choices. Record decisions with
          their rationale and evidence so the "why" is never lost.
        </p>
      </header>

      {decisionId ? (
        <section className="mb-6 rounded-xl border border-brand-200 bg-brand-50/40 p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-900">
              Opened decision
            </h2>
            <Link
              to="/decisions"
              className="text-xs font-medium text-brand-600 hover:text-brand-700"
            >
              Close details
            </Link>
          </div>
          {detailLoading ? (
            <LoadingState label="Loading decisionâ€¦" />
          ) : detailError ? (
            <ErrorState
              message={detailError}
              onRetry={() => void loadOpenedDecision()}
            />
          ) : openedDecision ? (
            <ul>
              <DecisionCard
                decision={openedDecision}
                entityName={
                  openedDecision.business_entity_id
                    ? entityNameById.get(openedDecision.business_entity_id) ?? null
                    : null
                }
                busy={deletingId === openedDecision.id}
                onDelete={() => void handleDelete(openedDecision)}
              />
            </ul>
          ) : null}
        </section>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,24rem)_1fr]">
        {/* Record form (Requirement 9.1) */}
        <div>
          <DecisionForm entities={entities} onCreated={handleCreated} />
        </div>

        {/* List (Requirement 9.3) */}
        <div>
          <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
            <div>
              <label
                htmlFor="filter-entity"
                className="block text-xs font-medium text-slate-600"
              >
                Business entity
              </label>
              <select
                id="filter-entity"
                value={entityFilter}
                onChange={(e) => setEntityFilter(e.target.value)}
                className="mt-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
              >
                <option value="">All entities</option>
                {entities.map((entity) => (
                  <option key={entity.id} value={entity.id}>
                    {entity.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {deleteError ? (
            <ErrorState message={deleteError} variant="alert" className="mb-4" />
          ) : null}

          {loading ? (
            <LoadingState variant="skeleton" label="Loading decisions…" />
          ) : error ? (
            <ErrorState message={error} onRetry={() => void loadDecisions()} />
          ) : decisions.length === 0 ? (
            <EmptyState
              title="No decisions recorded yet."
              description="Use the form to preserve the reasoning behind a choice."
            />
          ) : (
            <ul className="space-y-3">
              {decisions.map((decision) => (
                <DecisionCard
                  key={decision.id}
                  decision={decision}
                  entityName={
                    decision.business_entity_id
                      ? entityNameById.get(decision.business_entity_id) ?? null
                      : null
                  }
                  busy={deletingId === decision.id}
                  onDelete={() => void handleDelete(decision)}
                />
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
