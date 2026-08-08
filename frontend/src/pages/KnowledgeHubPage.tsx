// KnowledgeHubPage: the review surface of the knowledge base (Requirement 7).
//
// Responsibilities:
//   - List the organization's knowledge items, filterable by business entity
//     and (optionally) suggestion status (Req 7.3).
//   - Selecting an item loads its detail: summary, key points, evidence text,
//     and the actions and decisions linked to it (Req 7.4, 7.5).
//   - Confirm / reject a SUGGESTED item, updating both the detail and the list
//     in place (Req 7.1, 7.2).
//
// Every AI-produced knowledge item is a human-in-the-loop *suggestion*, so the
// SUGGESTED → CONFIRMED/REJECTED action is presented via the shared
// SuggestionCard, and evidence is always shown via the shared EvidenceBadge
// ("evidence everywhere"). All data flows through the real Core Engine API
// (src/api/knowledge.ts, src/api/businessEntities.ts); there is no hardcoded
// output.

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ApiError,
  businessEntitiesApi,
  getErrorMessage,
  knowledgeApi,
} from "@/api";
import type {
  BusinessEntity,
  KnowledgeDetail,
  KnowledgeItem,
  SuggestionStatus,
} from "@/api";
import SuggestionCard from "@/components/SuggestionCard";
import EvidenceBadge from "@/components/EvidenceBadge";
import {
  EmptyState,
  ErrorState,
  LoadingState,
} from "@/components/feedback";

const SUGGESTION_STATUSES: SuggestionStatus[] = [
  "SUGGESTED",
  "CONFIRMED",
  "REJECTED",
];

const STATUS_PILL: Record<SuggestionStatus, string> = {
  SUGGESTED: "bg-amber-50 text-amber-700 border-amber-200",
  CONFIRMED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  REJECTED: "bg-rose-50 text-rose-700 border-rose-200",
};

const ACTION_STATUS_PILL: Record<string, string> = {
  OPEN: "bg-sky-50 text-sky-700 border-sky-200",
  IN_PROGRESS: "bg-indigo-50 text-indigo-700 border-indigo-200",
  DONE: "bg-emerald-50 text-emerald-700 border-emerald-200",
  CANCELLED: "bg-slate-100 text-slate-500 border-slate-200",
};

function humanize(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString();
}

// ---------------------------------------------------------------------------
// List row
// ---------------------------------------------------------------------------

interface KnowledgeListRowProps {
  item: KnowledgeItem;
  entityName: string | null;
  selected: boolean;
  onSelect: () => void;
}

function KnowledgeListRow({
  item,
  entityName,
  selected,
  onSelect,
}: KnowledgeListRowProps) {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected}
        className={`flex w-full flex-col items-start gap-1 border-l-2 px-4 py-3 text-left transition ${
          selected
            ? "border-brand-500 bg-brand-50/60"
            : "border-transparent hover:bg-slate-50"
        }`}
      >
        <div className="flex w-full items-center justify-between gap-2">
          <span className="truncate text-sm font-medium text-slate-900">
            {item.summary || humanize(item.knowledge_type)}
          </span>
          <span
            className={`shrink-0 rounded border px-2 py-0.5 text-[11px] font-medium ${STATUS_PILL[item.status]}`}
          >
            {item.status}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span className="rounded bg-slate-100 px-1.5 py-0.5 text-slate-600">
            {humanize(item.knowledge_type)}
          </span>
          <span className="truncate">{entityName ?? "Unlinked entity"}</span>
        </div>
      </button>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

interface KnowledgeDetailPanelProps {
  knowledgeId: string;
  entityNameById: ReadonlyMap<string, string>;
  onStatusChanged: (item: KnowledgeItem) => void;
  onDeleted: (knowledgeId: string) => void;
}

function KnowledgeDetailPanel({
  knowledgeId,
  entityNameById,
  onStatusChanged,
  onDeleted,
}: KnowledgeDetailPanelProps) {
  const [detail, setDetail] = useState<KnowledgeDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadDetail = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await knowledgeApi.getDetail(knowledgeId);
      setDetail(result);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError("This knowledge item could not be found.");
      } else {
        setError("Could not load the knowledge item. Please retry.");
      }
    } finally {
      setLoading(false);
    }
  }, [knowledgeId]);

  useEffect(() => {
    void loadDetail();
  }, [loadDetail]);

  async function runStatusChange(
    fn: () => Promise<KnowledgeItem>,
  ): Promise<void> {
    if (busy) return;
    setBusy(true);
    setActionError(null);
    try {
      const updated = await fn();
      setDetail((current) =>
        current ? { ...current, knowledge_item: updated } : current,
      );
      onStatusChanged(updated);
    } catch (err) {
      setActionError(getErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return <LoadingState label="Loading knowledge item…" className="p-6" />;
  }

  if (error) {
    return (
      <ErrorState
        message={error}
        onRetry={() => void loadDetail()}
        className="m-6"
      />
    );
  }

  if (!detail) return null;

  const { knowledge_item: item, linked_actions, linked_decisions } = detail;
  const isSuggested = item.status === "SUGGESTED";
  const entityName = item.business_entity_id
    ? entityNameById.get(item.business_entity_id) ?? "Unknown entity"
    : null;

  async function handleDelete() {
    if (busy) return;
    const confirmed = window.confirm(
      "Permanently delete this knowledge item? Any actions linked to it are kept but detached. This cannot be undone.",
    );
    if (!confirmed) return;
    setBusy(true);
    setActionError(null);
    try {
      await knowledgeApi.remove(item.id);
      onDeleted(item.id);
    } catch (err) {
      setActionError(getErrorMessage(err));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5 p-6">
      {/* Suggestion header with confirm/reject (Req 7.1, 7.2) and evidence */}
      <SuggestionCard
        title={item.summary || humanize(item.knowledge_type)}
        subtitle={`${humanize(item.knowledge_type)} · ${entityName ?? "Unlinked entity"}`}
        status={item.status}
        evidenceText={item.evidence_text}
        onConfirm={
          isSuggested
            ? () => void runStatusChange(() => knowledgeApi.confirm(item.id))
            : undefined
        }
        onReject={
          isSuggested
            ? () => void runStatusChange(() => knowledgeApi.reject(item.id))
            : undefined
        }
        confirmLabel="Confirm knowledge"
        rejectLabel="Reject"
        busy={busy}
      >
        {/* Key points (Req 7.4) */}
        {item.key_points.length > 0 ? (
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
              Key points
            </div>
            <ul className="mt-1 list-inside list-disc space-y-1 text-sm text-slate-600">
              {item.key_points.map((point, index) => (
                <li key={`${point}-${index}`}>{point}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </SuggestionCard>

      {actionError ? <ErrorState message={actionError} variant="alert" /> : null}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => void handleDelete()}
          disabled={busy}
          className="text-xs font-medium text-red-500 underline-offset-2 transition hover:text-red-700 hover:underline disabled:cursor-not-allowed disabled:opacity-60"
        >
          Delete permanently
        </button>
      </div>

      {/* Linked actions (Req 7.4) */}
      <section>
        <h3 className="text-sm font-semibold text-slate-900">
          Linked actions
          <span className="ml-1 text-xs font-normal text-slate-400">
            ({linked_actions.length})
          </span>
        </h3>
        {linked_actions.length === 0 ? (
          <p className="mt-1 text-sm text-slate-500">
            No actions are linked to this knowledge item.
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {linked_actions.map((action) => (
              <li
                key={action.id}
                className="rounded-lg border border-slate-200 bg-white p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <Link
                    to={`/actions/${action.id}`}
                    className="text-sm font-medium text-slate-900 hover:text-brand-700"
                  >
                    {action.title}
                  </Link>
                  <span
                    className={`shrink-0 rounded border px-2 py-0.5 text-[11px] font-medium ${
                      ACTION_STATUS_PILL[action.status] ??
                      "bg-slate-100 text-slate-600 border-slate-200"
                    }`}
                  >
                    {action.status}
                  </span>
                </div>
                {action.description ? (
                  <p className="mt-1 text-sm text-slate-600">
                    {action.description}
                  </p>
                ) : null}
                <div className="mt-1 flex flex-wrap gap-3 text-xs text-slate-400">
                  <span>Due {formatDate(action.due_date)}</span>
                  {action.ai_generated ? <span>AI-generated</span> : null}
                </div>
                {action.evidence_text ? (
                  <EvidenceBadge text={action.evidence_text} className="mt-2" />
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Linked decisions (Req 7.4) */}
      <section>
        <h3 className="text-sm font-semibold text-slate-900">
          Linked decisions
          <span className="ml-1 text-xs font-normal text-slate-400">
            ({linked_decisions.length})
          </span>
        </h3>
        {linked_decisions.length === 0 ? (
          <p className="mt-1 text-sm text-slate-500">
            No decisions are linked to this knowledge item.
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {linked_decisions.map((decision) => (
              <li
                key={decision.id}
                className="rounded-lg border border-slate-200 bg-white p-3"
              >
                <Link
                  to={`/decisions/${decision.id}`}
                  className="text-sm font-medium text-slate-900 hover:text-brand-700"
                >
                  {decision.title}
                </Link>
                <p className="mt-1 text-sm text-slate-700">
                  {decision.decision}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {decision.rationale}
                </p>
                <div className="mt-1 text-xs text-slate-400">
                  Decided {formatDate(decision.decided_at)}
                </div>
                <EvidenceBadge
                  text={decision.evidence_text}
                  className="mt-2"
                />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function KnowledgeHubPage() {
  const { knowledgeId } = useParams<{ knowledgeId: string }>();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [items, setItems] = useState<KnowledgeItem[]>([]);
  const [entities, setEntities] = useState<BusinessEntity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const businessEntityQuery = searchParams.get("business_entity_id") ?? "";
  const [entityFilter, setEntityFilter] = useState<string>(businessEntityQuery);
  const [statusFilter, setStatusFilter] = useState<SuggestionStatus | "">("");
  const selectedId = knowledgeId ?? null;

  useEffect(() => {
    setEntityFilter(businessEntityQuery);
  }, [businessEntityQuery]);

  const filters = useMemo(
    () => ({
      businessEntityId: entityFilter || undefined,
      status: statusFilter || undefined,
    }),
    [entityFilter, statusFilter],
  );

  // Business entities power the filter dropdown; a load failure here should not
  // block the knowledge list, so it is intentionally best-effort.
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

  const loadItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await knowledgeApi.list(filters);
      setItems(result);
    } catch {
      setError("Could not load the knowledge base. Please retry.");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void loadItems();
  }, [loadItems]);

  const handleStatusChanged = useCallback((updated: KnowledgeItem) => {
    setItems((current) =>
      current.map((it) => (it.id === updated.id ? updated : it)),
    );
  }, []);

  const handleDeleted = useCallback(
    (deletedKnowledgeId: string) => {
      setItems((current) =>
        current.filter((it) => it.id !== deletedKnowledgeId),
      );
      if (knowledgeId === deletedKnowledgeId) {
        const query = searchParams.toString();
        navigate(`/knowledge${query ? `?${query}` : ""}`, { replace: true });
      }
    },
    [knowledgeId, navigate, searchParams],
  );

  const openKnowledge = useCallback(
    (id: string) => {
      const query = searchParams.toString();
      navigate(`/knowledge/${id}${query ? `?${query}` : ""}`);
    },
    [navigate, searchParams],
  );

  const changeEntityFilter = useCallback(
    (value: string) => {
      setEntityFilter(value);
      const next = new URLSearchParams(searchParams);
      if (value) next.set("business_entity_id", value);
      else next.delete("business_entity_id");
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  return (
    <section className="mx-auto max-w-6xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-2xl font-semibold text-slate-900">Knowledge Hub</h1>
        <p className="mt-1 text-sm text-slate-500">
          Review evidence-backed knowledge, filter by business entity, and
          confirm or reject suggestions before they enter business memory.
        </p>
      </header>

      {/* Filters (Requirement 7.3) */}
      <div className="mb-6 flex flex-wrap items-end gap-4">
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
            onChange={(e) => changeEntityFilter(e.target.value)}
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

        <div>
          <label
            htmlFor="filter-status"
            className="block text-xs font-medium text-slate-600"
          >
            Status
          </label>
          <select
            id="filter-status"
            value={statusFilter}
            onChange={(e) =>
              setStatusFilter(e.target.value as SuggestionStatus | "")
            }
            className="mt-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="">All statuses</option>
            {SUGGESTION_STATUSES.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,20rem)_1fr]">
        {/* List */}
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
          {loading ? (
            <LoadingState label="Loading knowledge…" className="p-4" />
          ) : error ? (
            <ErrorState
              message={error}
              onRetry={() => void loadItems()}
              className="m-4"
            />
          ) : items.length === 0 ? (
            <EmptyState
              variant="plain"
              title="No knowledge items match these filters."
              description="Extract knowledge from the Source Inbox to populate the hub."
              className="p-4"
            />
          ) : (
            <ul className="divide-y divide-slate-100">
              {items.map((item) => (
                <KnowledgeListRow
                  key={item.id}
                  item={item}
                  entityName={
                    item.business_entity_id
                      ? entityNameById.get(item.business_entity_id) ?? null
                      : null
                  }
                  selected={item.id === selectedId}
                  onSelect={() => openKnowledge(item.id)}
                />
              ))}
            </ul>
          )}
        </div>

        {/* Detail */}
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
          {selectedId ? (
            <KnowledgeDetailPanel
              key={selectedId}
              knowledgeId={selectedId}
              entityNameById={entityNameById}
              onStatusChanged={handleStatusChanged}
              onDeleted={handleDeleted}
            />
          ) : (
            <div className="flex h-full min-h-[16rem] items-center justify-center p-6 text-center text-sm text-slate-500">
              Select a knowledge item to see its summary, key points, evidence,
              and linked actions and decisions.
            </div>
          )}
        </div>
      </div>

      {selectedId ? (
        <div className="mt-4 text-right">
          <Link
            to={`/knowledge${searchParams.toString() ? `?${searchParams.toString()}` : ""}`}
            className="text-sm font-medium text-brand-600 hover:text-brand-700"
          >
            Close details
          </Link>
        </div>
      ) : null}
    </section>
  );
}
