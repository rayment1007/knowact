// ActionCenterPage: the "what needs to happen" surface of business memory
// (Requirement 8).
//
// Responsibilities:
//   - List the organization's action items, filterable by status and business
//     entity (Requirement 8.3).
//   - Mark an open action as done via PATCH (Requirement 8.4), updating the row
//     in place.
//   - Show an origin indicator for every action — AI-generated vs recorded by a
//     human — derived from the `ai_generated` flag (Requirement 8.5 context).
//   - Optionally record a manual action (Requirement 8.1); manual actions are
//     created with `ai_generated=false` by the backend.
//
// Evidence is always shown via the shared EvidenceBadge ("evidence
// everywhere"). All data flows through the real Core Engine API
// (src/api/actions.ts, src/api/businessEntities.ts); there is no hardcoded
// output.

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  actionsApi,
  businessEntitiesApi,
  calendarApi,
  getErrorMessage,
  parseFieldErrors,
} from "@/api";
import type {
  ActionCreate,
  ActionItem,
  ActionStatus,
  BusinessEntity,
  CalendarEventLink,
  FieldErrors,
} from "@/api";
import AddToCalendar from "@/components/AddToCalendar";
import EvidenceBadge from "@/components/EvidenceBadge";
import { EmptyState, ErrorState, FieldError, LoadingState } from "@/components/feedback";

const ACTION_STATUSES: ActionStatus[] = [
  "OPEN",
  "IN_PROGRESS",
  "DONE",
  "CANCELLED",
];

const STATUS_PILL: Record<ActionStatus, string> = {
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
// Origin indicator (Requirement 8.5 context): AI-generated vs human-recorded.
// ---------------------------------------------------------------------------

function OriginBadge({ aiGenerated }: { aiGenerated: boolean }) {
  return aiGenerated ? (
    <span className="inline-flex items-center gap-1 rounded border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-700">
      <span aria-hidden>✦</span> AI-generated
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-medium text-slate-600">
      <span aria-hidden>✓</span> Human
    </span>
  );
}

// ---------------------------------------------------------------------------
// Action row
// ---------------------------------------------------------------------------

interface ActionRowProps {
  action: ActionItem;
  entityName: string | null;
  busy: boolean;
  onMarkDone: () => void;
  onDelete: () => void;
  calendarLink: CalendarEventLink | null;
  onCalendarChange: (link: CalendarEventLink) => void;
}

function ActionRow({
  action,
  entityName,
  busy,
  onMarkDone,
  onDelete,
  calendarLink,
  onCalendarChange,
}: ActionRowProps) {
  const isClosed = action.status === "DONE" || action.status === "CANCELLED";
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link
              to={`/actions/${action.id}`}
              className="text-sm font-medium text-slate-900 hover:text-brand-700"
            >
              {action.title}
            </Link>
            <span
              className={`shrink-0 rounded border px-2 py-0.5 text-[11px] font-medium ${STATUS_PILL[action.status]}`}
            >
              {humanize(action.status)}
            </span>
            <OriginBadge aiGenerated={action.ai_generated} />
          </div>
          {action.description ? (
            <p className="mt-1 text-sm text-slate-600">{action.description}</p>
          ) : null}
          <div className="mt-1 flex flex-wrap gap-3 text-xs text-slate-400">
            <span>{entityName ?? "Unlinked entity"}</span>
            <span>Due {formatDate(action.due_date)}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!isClosed ? (
            <button
              type="button"
              onClick={onMarkDone}
              disabled={busy}
              className="rounded-md border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-sm font-medium text-emerald-700 hover:bg-emerald-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {busy ? "Saving…" : "Mark done"}
            </button>
          ) : null}
          <button
            type="button"
            onClick={onDelete}
            disabled={busy}
            className="rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Delete
          </button>
        </div>
      </div>
      {action.evidence_text ? (
        <EvidenceBadge text={action.evidence_text} className="mt-2" />
      ) : null}
      {/* Add-to-Google-Calendar affordance for a confirmed action (Req 28). A
          withdrawn (CANCELLED) action cannot be scheduled. */}
      {action.status !== "CANCELLED" ? (
        <AddToCalendar
          actionId={action.id}
          link={calendarLink}
          onChange={onCalendarChange}
        />
      ) : null}
      {calendarLink ? (
        <Link
          to={`/calendar/${calendarLink.id}`}
          className="mt-2 inline-flex text-xs font-medium text-brand-600 hover:text-brand-700"
        >
          View calendar sync details
        </Link>
      ) : null}
    </li>
  );
}

// ---------------------------------------------------------------------------
// Manual action form (Requirement 8.1)
// ---------------------------------------------------------------------------

interface ManualActionFormProps {
  entities: BusinessEntity[];
  onCreated: (action: ActionItem) => void;
}

function ManualActionForm({ entities, onCreated }: ManualActionFormProps) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [entityId, setEntityId] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [evidenceText, setEvidenceText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});

  function reset() {
    setTitle("");
    setDescription("");
    setEntityId("");
    setDueDate("");
    setEvidenceText("");
    setError(null);
    setFieldErrors({});
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (submitting) return;
    if (!title.trim()) {
      setError("A title is required.");
      setFieldErrors({ title: "A title is required." });
      return;
    }
    setSubmitting(true);
    setError(null);
    setFieldErrors({});
    const payload: ActionCreate = {
      title: title.trim(),
      description: description.trim() || undefined,
      business_entity_id: entityId || undefined,
      due_date: dueDate || undefined,
      evidence_text: evidenceText.trim() || undefined,
    };
    try {
      const created = await actionsApi.create(payload);
      onCreated(created);
      reset();
      setOpen(false);
    } catch (err) {
      if (err instanceof ApiError && err.status === 422) {
        setFieldErrors(parseFieldErrors(err));
      }
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
      >
        Record action
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Record action</h2>
        <button
          type="button"
          onClick={() => {
            reset();
            setOpen(false);
          }}
          className="text-sm text-slate-500 hover:text-slate-700"
        >
          Cancel
        </button>
      </div>

      <div>
        <label
          htmlFor="action-title"
          className="block text-xs font-medium text-slate-600"
        >
          Title
        </label>
        <input
          id="action-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          aria-invalid={Boolean(fieldErrors.title)}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          placeholder="What needs to happen?"
        />
        <FieldError message={fieldErrors.title} />
      </div>

      <div>
        <label
          htmlFor="action-description"
          className="block text-xs font-medium text-slate-600"
        >
          Description
        </label>
        <textarea
          id="action-description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label
            htmlFor="action-entity"
            className="block text-xs font-medium text-slate-600"
          >
            Business entity
          </label>
          <select
            id="action-entity"
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
            htmlFor="action-due"
            className="block text-xs font-medium text-slate-600"
          >
            Due date
          </label>
          <input
            id="action-due"
            type="date"
            value={dueDate}
            onChange={(e) => setDueDate(e.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          />
        </div>
      </div>

      <div>
        <label
          htmlFor="action-evidence"
          className="block text-xs font-medium text-slate-600"
        >
          Evidence
        </label>
        <textarea
          id="action-evidence"
          value={evidenceText}
          onChange={(e) => setEvidenceText(e.target.value)}
          rows={2}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          placeholder="Source text supporting this action"
        />
      </div>

      {error ? <ErrorState message={error} variant="alert" /> : null}

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? "Saving…" : "Save action"}
        </button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ActionCenterPage() {
  const { actionId, calendarLinkId } = useParams<{
    actionId: string;
    calendarLinkId: string;
  }>();
  const navigate = useNavigate();
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [entities, setEntities] = useState<BusinessEntity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [calendarLinks, setCalendarLinks] = useState<
    Map<string, CalendarEventLink>
  >(new Map());
  const [openedAction, setOpenedAction] = useState<ActionItem | null>(null);
  const [openedCalendarLink, setOpenedCalendarLink] =
    useState<CalendarEventLink | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<ActionStatus | "">("");
  const [entityFilter, setEntityFilter] = useState<string>("");

  const filters = useMemo(
    () => ({
      status: statusFilter || undefined,
      businessEntityId: entityFilter || undefined,
    }),
    [statusFilter, entityFilter],
  );

  const loadOpenedDetail = useCallback(async () => {
    if (!actionId && !calendarLinkId) {
      setOpenedAction(null);
      setOpenedCalendarLink(null);
      setDetailError(null);
      return;
    }

    setDetailLoading(true);
    setDetailError(null);
    setOpenedAction(null);
    setOpenedCalendarLink(null);
    try {
      if (actionId) {
        setOpenedAction(await actionsApi.get(actionId));
      } else if (calendarLinkId) {
        const link = await calendarApi.getLink(calendarLinkId);
        setOpenedCalendarLink(link);
        if (link.action_item_id) {
          try {
            setOpenedAction(await actionsApi.get(link.action_item_id));
          } catch (err) {
            if (!(err instanceof ApiError && err.status === 404)) throw err;
          }
        }
      }
    } catch (err) {
      setDetailError(
        err instanceof ApiError && err.status === 404
          ? "This action or calendar link could not be found."
          : "Could not load the opened item. Please retry.",
      );
    } finally {
      setDetailLoading(false);
    }
  }, [actionId, calendarLinkId]);

  useEffect(() => {
    void loadOpenedDetail();
  }, [loadOpenedDetail]);

  // Business entities power the filter dropdown and the manual form; a load
  // failure here should not block the action list, so it is best-effort.
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

  // Existing calendar event links, keyed by action, so each row can show its
  // sync state (Requirement 28.6). Best-effort: a failure never blocks the list.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const links = await calendarApi.listLinks();
        if (cancelled) return;
        const map = new Map<string, CalendarEventLink>();
        for (const link of links) {
          if (link.action_item_id) map.set(link.action_item_id, link);
        }
        setCalendarLinks(map);
      } catch {
        if (!cancelled) setCalendarLinks(new Map());
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleCalendarChange = useCallback((link: CalendarEventLink) => {
    if (!link.action_item_id) return;
    setCalendarLinks((current) => {
      const next = new Map(current);
      next.set(link.action_item_id as string, link);
      return next;
    });
    setOpenedCalendarLink((current) =>
      current?.id === link.id ? link : current,
    );
  }, []);

  const entityNameById = useMemo(() => {
    const map = new Map<string, string>();
    for (const entity of entities) map.set(entity.id, entity.name);
    return map;
  }, [entities]);

  const loadActions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await actionsApi.list(filters);
      setActions(result);
    } catch {
      setError("Could not load actions. Please retry.");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void loadActions();
  }, [loadActions]);

  const handleMarkDone = useCallback(
    async (action: ActionItem) => {
      if (busyId) return;
      setBusyId(action.id);
      setActionError(null);
      try {
        const updated = await actionsApi.update(action.id, { status: "DONE" });
        setActions((current) =>
          current.map((it) => (it.id === updated.id ? updated : it)),
        );
        setOpenedAction((current) =>
          current?.id === updated.id ? updated : current,
        );
      } catch (err) {
        setActionError(getErrorMessage(err, "Could not update the action. Please retry."));
      } finally {
        setBusyId(null);
      }
    },
    [busyId],
  );

  const handleCreated = useCallback((created: ActionItem) => {
    // Prepend so the newest action is visible; the next filtered reload will
    // reconcile it against any active filters.
    setActions((current) => [created, ...current]);
  }, []);

  const handleDelete = useCallback(
    async (action: ActionItem) => {
      if (busyId) return;
      const confirmed = window.confirm(
        `Permanently delete the action "${action.title}"? Any Google Calendar event for it is also removed. This cannot be undone.`,
      );
      if (!confirmed) return;
      setBusyId(action.id);
      setActionError(null);
      try {
        await actionsApi.remove(action.id);
        setActions((current) => current.filter((it) => it.id !== action.id));
        if (openedAction?.id === action.id) {
          setOpenedAction(null);
          setOpenedCalendarLink(null);
          navigate("/actions", { replace: true });
        }
      } catch (err) {
        setActionError(
          getErrorMessage(err, "Could not delete the action. Please retry."),
        );
      } finally {
        setBusyId(null);
      }
    },
    [busyId, navigate, openedAction?.id],
  );

  return (
    <section className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">
            Action Center
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            Track what needs to happen. Filter by status or business entity,
            mark actions done, and see whether each was AI-generated or recorded
            by a human.
          </p>
        </div>
        <ManualActionForm entities={entities} onCreated={handleCreated} />
      </header>

      {actionId || calendarLinkId ? (
        <section className="mb-6 rounded-xl border border-brand-200 bg-brand-50/40 p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-900">
              {calendarLinkId ? "Opened calendar link" : "Opened action"}
            </h2>
            <Link
              to="/actions"
              className="text-xs font-medium text-brand-600 hover:text-brand-700"
            >
              Close details
            </Link>
          </div>

          {detailLoading ? (
            <LoadingState label="Loading opened itemâ€¦" />
          ) : detailError ? (
            <ErrorState
              message={detailError}
              onRetry={() => void loadOpenedDetail()}
            />
          ) : (
            <>
              {openedCalendarLink ? (
                <div className="mb-3 rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-600">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-slate-900">
                      Google Calendar sync
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                      {humanize(openedCalendarLink.sync_status)}
                    </span>
                  </div>
                  <dl className="mt-2 grid gap-1 text-xs sm:grid-cols-2">
                    <div>
                      <dt className="inline font-medium text-slate-500">Calendar: </dt>
                      <dd className="inline">{openedCalendarLink.google_calendar_id}</dd>
                    </div>
                    <div>
                      <dt className="inline font-medium text-slate-500">Last synced: </dt>
                      <dd className="inline">{formatDate(openedCalendarLink.last_synced_at)}</dd>
                    </div>
                  </dl>
                  {openedCalendarLink.last_error ? (
                    <p className="mt-2 text-xs text-red-600">
                      {openedCalendarLink.last_error}
                    </p>
                  ) : null}
                </div>
              ) : null}

              {openedAction ? (
                <ul>
                  <ActionRow
                    action={openedAction}
                    entityName={
                      openedAction.business_entity_id
                        ? entityNameById.get(openedAction.business_entity_id) ?? null
                        : null
                    }
                    busy={busyId === openedAction.id}
                    onMarkDone={() => void handleMarkDone(openedAction)}
                    onDelete={() => void handleDelete(openedAction)}
                    calendarLink={
                      openedCalendarLink ??
                      calendarLinks.get(openedAction.id) ??
                      null
                    }
                    onCalendarChange={handleCalendarChange}
                  />
                </ul>
              ) : openedCalendarLink ? (
                <p className="text-sm text-slate-500">
                  This calendar link is not attached to an action.
                </p>
              ) : null}
            </>
          )}
        </section>
      ) : null}

      {/* Filters (Requirement 8.3) */}
      <div className="mb-6 flex flex-wrap items-end gap-4">
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
              setStatusFilter(e.target.value as ActionStatus | "")
            }
            className="mt-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm text-slate-900 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500"
          >
            <option value="">All statuses</option>
            {ACTION_STATUSES.map((status) => (
              <option key={status} value={status}>
                {humanize(status)}
              </option>
            ))}
          </select>
        </div>

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

      {actionError ? (
        <ErrorState message={actionError} variant="alert" className="mb-4" />
      ) : null}

      {loading ? (
        <LoadingState variant="skeleton" label="Loading actions…" />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void loadActions()} />
      ) : actions.length === 0 ? (
        <EmptyState
          title="No actions match these filters."
          description="Record an action above, or generate actions from confirmed knowledge in the Knowledge Hub."
        />
      ) : (
        <ul className="space-y-3">
          {actions.map((action) => (
            <ActionRow
              key={action.id}
              action={action}
              entityName={
                action.business_entity_id
                  ? entityNameById.get(action.business_entity_id) ?? null
                  : null
              }
              busy={busyId === action.id}
              onMarkDone={() => void handleMarkDone(action)}
              onDelete={() => void handleDelete(action)}
              calendarLink={calendarLinks.get(action.id) ?? null}
              onCalendarChange={handleCalendarChange}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
