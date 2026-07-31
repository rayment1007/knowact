// AddToCalendar: the "Add to Google Calendar" affordance for a confirmed
// action (Requirement 28).
//
// Responsibilities:
//   - Let the user explicitly add a confirmed action to Google Calendar
//     (Requirement 28.1) — never automatically (Requirement 28.4).
//   - Let the user choose the target calendar, set start/end times, mark the
//     event all-day for a due-date-only action, set a reminder, and edit the
//     summary/description before confirming (Requirement 28.3).
//   - Display the current sync state (SYNCED / PENDING / FAILED) and offer a
//     retry for a failed sync (Requirement 28.6).
//
// All data flows through the real Calendar + Integrations APIs; there is no
// hardcoded output. A non-confirmed action is rejected server-side (409) and
// surfaced inline.

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  calendarApi,
  getErrorMessage,
  integrationsApi,
} from "@/api";
import type {
  CalendarAddRequest,
  CalendarEventLink,
  CalendarSyncStatus,
  CalendarView,
  IntegrationConnection,
} from "@/api";
import { ErrorState } from "@/components/feedback";

const SYNC_PILL: Record<CalendarSyncStatus, string> = {
  PENDING: "bg-amber-50 text-amber-700 border-amber-200",
  SYNCED: "bg-emerald-50 text-emerald-700 border-emerald-200",
  UPDATE_PENDING: "bg-amber-50 text-amber-700 border-amber-200",
  CANCEL_PENDING: "bg-amber-50 text-amber-700 border-amber-200",
  CANCELLED: "bg-slate-100 text-slate-500 border-slate-200",
  FAILED: "bg-rose-50 text-rose-700 border-rose-200",
};

function humanizeStatus(status: CalendarSyncStatus): string {
  return status
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

const REMINDER_OPTIONS: { label: string; value: number | "" }[] = [
  { label: "No reminder", value: "" },
  { label: "10 minutes before", value: 10 },
  { label: "30 minutes before", value: 30 },
  { label: "1 hour before", value: 60 },
  { label: "1 day before", value: 1440 },
];

interface SyncStateBadgeProps {
  link: CalendarEventLink;
  busy: boolean;
  onRetry: () => void;
}

/** The sync-state display for a link that already exists (Requirement 28.6). */
function SyncStateBadge({ link, busy, onRetry }: SyncStateBadgeProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span
        className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-[11px] font-medium ${SYNC_PILL[link.sync_status]}`}
      >
        <span aria-hidden>📅</span> Calendar: {humanizeStatus(link.sync_status)}
      </span>
      {link.sync_status === "FAILED" ? (
        <button
          type="button"
          onClick={onRetry}
          disabled={busy}
          className="rounded border border-rose-300 bg-rose-50 px-2 py-0.5 text-[11px] font-medium text-rose-700 hover:bg-rose-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "Retrying…" : "Retry sync"}
        </button>
      ) : null}
      {link.last_error ? (
        <span className="text-[11px] text-rose-600">{link.last_error}</span>
      ) : null}
    </div>
  );
}

interface AddToCalendarProps {
  actionId: string;
  /** An existing link for this action, if it has already been scheduled. */
  link: CalendarEventLink | null;
  /** Called whenever the link is created/updated so the parent can refresh. */
  onChange: (link: CalendarEventLink) => void;
}

export default function AddToCalendar({
  actionId,
  link,
  onChange,
}: AddToCalendarProps) {
  const [open, setOpen] = useState(false);
  const [connections, setConnections] = useState<IntegrationConnection[]>([]);
  const [connectionId, setConnectionId] = useState("");
  const [calendars, setCalendars] = useState<CalendarView[]>([]);
  const [calendarId, setCalendarId] = useState("");
  const [summary, setSummary] = useState("");
  const [description, setDescription] = useState("");
  const [allDay, setAllDay] = useState(false);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [allDayDate, setAllDayDate] = useState("");
  const [reminder, setReminder] = useState<number | "">("");
  const [submitting, setSubmitting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadingConnections, setLoadingConnections] = useState(false);

  // Load the user's CONNECTED Google Calendar connections when the form opens.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoadingConnections(true);
    void (async () => {
      try {
        const all = await integrationsApi.list();
        const calendarConnections = all.filter(
          (c) => c.service === "GOOGLE_CALENDAR" && c.status === "CONNECTED",
        );
        if (cancelled) return;
        setConnections(calendarConnections);
        if (calendarConnections.length > 0) {
          setConnectionId((current) => current || calendarConnections[0].id);
        }
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      } finally {
        if (!cancelled) setLoadingConnections(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  // Load writable calendars for the selected connection (Requirement 28.3).
  useEffect(() => {
    if (!open || !connectionId) return;
    let cancelled = false;
    void (async () => {
      try {
        const result = await calendarApi.listCalendars(connectionId);
        if (cancelled) return;
        setCalendars(result);
        const preferred =
          result.find((c) => c.primary)?.calendar_id ??
          result[0]?.calendar_id ??
          "";
        setCalendarId((current) => current || preferred);
      } catch (err) {
        if (!cancelled) setError(getErrorMessage(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, connectionId]);

  const handleSubmit = useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (submitting || !connectionId || !calendarId) {
        if (!connectionId) setError("Connect Google Calendar first.");
        return;
      }
      setSubmitting(true);
      setError(null);
      const request: CalendarAddRequest = {
        connection_id: connectionId,
        google_calendar_id: calendarId,
        summary: summary.trim() || undefined,
        description: description.trim() || undefined,
        all_day: allDay,
        all_day_date: allDay && allDayDate ? allDayDate : undefined,
        start: !allDay && start ? new Date(start).toISOString() : undefined,
        end: !allDay && end ? new Date(end).toISOString() : undefined,
        reminder_minutes: reminder === "" ? [] : [reminder],
      };
      try {
        const created = await calendarApi.addActionToCalendar(
          actionId,
          request,
        );
        onChange(created);
        setOpen(false);
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setError("Only a confirmed action can be added to a calendar.");
        } else {
          setError(getErrorMessage(err));
        }
      } finally {
        setSubmitting(false);
      }
    },
    [
      submitting,
      connectionId,
      calendarId,
      summary,
      description,
      allDay,
      allDayDate,
      start,
      end,
      reminder,
      actionId,
      onChange,
    ],
  );

  const handleRetry = useCallback(async () => {
    if (!link || retrying) return;
    setRetrying(true);
    setError(null);
    try {
      const updated = await calendarApi.retry(link.id);
      onChange(updated);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setRetrying(false);
    }
  }, [link, retrying, onChange]);

  // Already scheduled: show the sync state + retry rather than the add form.
  if (link && link.sync_status !== "CANCELLED") {
    return (
      <div className="mt-2">
        <SyncStateBadge link={link} busy={retrying} onRetry={() => void handleRetry()} />
        {error ? <ErrorState message={error} variant="alert" className="mt-2" /> : null}
      </div>
    );
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-2 inline-flex items-center gap-1 rounded-md border border-sky-300 bg-sky-50 px-3 py-1 text-xs font-medium text-sky-700 hover:bg-sky-100"
      >
        <span aria-hidden>📅</span> Add to Google Calendar
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="mt-2 space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-900">
          Add to Google Calendar
        </h3>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="text-xs text-slate-500 hover:text-slate-700"
        >
          Cancel
        </button>
      </div>

      {loadingConnections ? (
        <p className="text-xs text-slate-500">Loading connections…</p>
      ) : connections.length === 0 ? (
        <p className="text-xs text-amber-700">
          No connected Google Calendar. Connect one on the Integrations page
          first.
        </p>
      ) : (
        <>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="block text-xs font-medium text-slate-600">
              Connection
              <select
                value={connectionId}
                onChange={(e) => {
                  setConnectionId(e.target.value);
                  setCalendarId("");
                }}
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              >
                {connections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.account_email}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs font-medium text-slate-600">
              Calendar
              <select
                value={calendarId}
                onChange={(e) => setCalendarId(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              >
                {calendars.map((c) => (
                  <option key={c.calendar_id} value={c.calendar_id}>
                    {c.summary}
                    {c.primary ? " (primary)" : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <label className="block text-xs font-medium text-slate-600">
            Title
            <input
              type="text"
              value={summary}
              onChange={(e) => setSummary(e.target.value)}
              placeholder="Defaults to the action title"
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>

          <label className="block text-xs font-medium text-slate-600">
            Description
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="Defaults to the action description"
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            />
          </label>

          <label className="flex items-center gap-2 text-xs font-medium text-slate-600">
            <input
              type="checkbox"
              checked={allDay}
              onChange={(e) => setAllDay(e.target.checked)}
            />
            All-day event (for a due-date-only action)
          </label>

          {allDay ? (
            <label className="block text-xs font-medium text-slate-600">
              Date
              <input
                type="date"
                value={allDayDate}
                onChange={(e) => setAllDayDate(e.target.value)}
                className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
              />
            </label>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="block text-xs font-medium text-slate-600">
                Start
                <input
                  type="datetime-local"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                  className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                />
              </label>
              <label className="block text-xs font-medium text-slate-600">
                End
                <input
                  type="datetime-local"
                  value={end}
                  onChange={(e) => setEnd(e.target.value)}
                  className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
                />
              </label>
            </div>
          )}

          <label className="block text-xs font-medium text-slate-600">
            Reminder
            <select
              value={reminder === "" ? "" : String(reminder)}
              onChange={(e) =>
                setReminder(e.target.value === "" ? "" : Number(e.target.value))
              }
              className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
            >
              {REMINDER_OPTIONS.map((opt) => (
                <option key={opt.label} value={opt.value === "" ? "" : opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>

          {error ? <ErrorState message={error} variant="alert" /> : null}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={submitting || !calendarId}
              className="rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Adding…" : "Add to calendar"}
            </button>
          </div>
        </>
      )}
    </form>
  );
}
