// GmailSyncPage: configure an initial Gmail import and review ingested email
// (Requirements 26, 27).
//
// Responsibilities:
//   - Pick a connected Gmail integration (from `GET /api/integrations`).
//   - Configure and start the initial sync (date range / labels / sent mail /
//     attachment handling / storage policy) via
//     `POST /api/gmail/{id}/initial-sync` (Requirements 26.1-26.6), and run a
//     manual `sync-now` (Requirement 27.8).
//   - Review ingested messages (`GET /api/gmail/messages`) and AI-extracted
//     task suggestions (`GET /api/gmail/suggestions`): confirm / edit / reject /
//     dismiss each suggestion, and mark a sender/domain personal or irrelevant
//     as a negative signal (Requirement 27.7).
//
// All AI outputs are SUGGESTED and require explicit human confirmation before
// they mutate business memory. Loading / empty / error states reuse the shared
// feedback components; suggestions reuse the shared SuggestionCard.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, gmailApi, integrationsApi } from "@/api";
import type {
  AttachmentHandling,
  EmailMessageRecord,
  EmailTaskSuggestion,
  InitialSyncOptions,
  IntegrationConnection,
  SenderSignalType,
  StoragePolicy,
  SyncRun,
} from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/feedback";
import SuggestionCard from "@/components/SuggestionCard";

const DATE_RANGES: (7 | 30 | 90)[] = [7, 30, 90];
const ATTACHMENT_OPTIONS: AttachmentHandling[] = [
  "IGNORE",
  "METADATA_ONLY",
  "STORE",
];
const STORAGE_OPTIONS: StoragePolicy[] = ["RAW_AND_EXTRACTED", "EXTRACTED_ONLY"];

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function describeError(err: unknown, verb: string): string {
  if (err instanceof ApiError) {
    if (err.status === 404) return "That item no longer exists. Refreshed.";
    if (err.status === 422) return "Those sync options are invalid. Please review.";
    return `Could not ${verb} (${err.status}). Please retry.`;
  }
  return `Could not ${verb}. Please retry.`;
}

interface EmailMessageCardProps {
  record: EmailMessageRecord;
  busy: boolean;
  onDelete: () => void;
}

function EmailMessageCard({ record, busy, onDelete }: EmailMessageCardProps) {
  return (
    <li className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            to={`/emails/${record.id}`}
            className="block truncate text-sm font-medium text-slate-900 hover:text-brand-700"
          >
            {record.subject || "(no subject)"}
          </Link>
          <p className="mt-0.5 truncate text-xs text-slate-500">
            {record.sender} · {formatDateTime(record.received_at)}
          </p>
          <p className="mt-1 truncate text-xs text-slate-400">
            To {record.recipients.join(", ") || "unknown recipient"}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-2">
          <div className="flex flex-wrap justify-end gap-1">
            {record.labels.map((label) => (
              <span
                key={label}
                className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500"
              >
                {label}
              </span>
            ))}
            {record.has_attachments ? (
              <span className="rounded bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                attachment
              </span>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onDelete}
            disabled={busy}
            className="text-xs font-medium text-red-500 underline-offset-2 transition hover:text-red-700 hover:underline disabled:opacity-60"
          >
            Delete
          </button>
        </div>
      </div>
    </li>
  );
}

interface SyncFormProps {
  disabled: boolean;
  onSubmit: (options: InitialSyncOptions) => void;
}

function InitialSyncForm({ disabled, onSubmit }: SyncFormProps) {
  const [dateRange, setDateRange] = useState<7 | 30 | 90>(30);
  const [labels, setLabels] = useState("");
  const [includeSent, setIncludeSent] = useState(false);
  const [attachment, setAttachment] =
    useState<AttachmentHandling>("METADATA_ONLY");
  const [storage, setStorage] = useState<StoragePolicy>("EXTRACTED_ONLY");

  function submit() {
    const parsedLabels = labels
      .split(",")
      .map((label) => label.trim())
      .filter((label) => label !== "");
    onSubmit({
      date_range_days: dateRange,
      labels: parsedLabels.length > 0 ? parsedLabels : null,
      include_sent: includeSent,
      attachment_handling: attachment,
      storage_policy: storage,
    });
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-900">
        Configure initial sync
      </h2>

      <div className="mt-4 space-y-4">
        <div>
          <span className="text-xs font-medium text-slate-600">Date range</span>
          <div className="mt-1 flex gap-2">
            {DATE_RANGES.map((days) => (
              <button
                key={days}
                type="button"
                onClick={() => setDateRange(days)}
                className={`rounded-md border px-3 py-1.5 text-sm font-medium transition ${
                  dateRange === days
                    ? "border-brand-500 bg-brand-50 text-brand-700"
                    : "border-slate-300 text-slate-600 hover:bg-slate-100"
                }`}
              >
                {days} days
              </button>
            ))}
          </div>
        </div>

        <div>
          <label
            htmlFor="gmail-labels"
            className="text-xs font-medium text-slate-600"
          >
            Labels (comma-separated, optional)
          </label>
          <input
            id="gmail-labels"
            type="text"
            value={labels}
            onChange={(event) => setLabels(event.target.value)}
            placeholder="INBOX, IMPORTANT"
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
          />
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={includeSent}
            onChange={(event) => setIncludeSent(event.target.checked)}
          />
          Include sent mail
        </label>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label
              htmlFor="gmail-attachments"
              className="text-xs font-medium text-slate-600"
            >
              Attachment handling
            </label>
            <select
              id="gmail-attachments"
              value={attachment}
              onChange={(event) =>
                setAttachment(event.target.value as AttachmentHandling)
              }
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
            >
              {ATTACHMENT_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label
              htmlFor="gmail-storage"
              className="text-xs font-medium text-slate-600"
            >
              Storage policy
            </label>
            <select
              id="gmail-storage"
              value={storage}
              onChange={(event) =>
                setStorage(event.target.value as StoragePolicy)
              }
              className="mt-1 w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
            >
              {STORAGE_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </div>
        </div>

        <button
          type="button"
          onClick={submit}
          disabled={disabled}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Start initial sync
        </button>
      </div>
    </div>
  );
}

interface SuggestionItemProps {
  suggestion: EmailTaskSuggestion;
  busy: boolean;
  onConfirm: (id: string) => void;
  onReject: (id: string) => void;
  onDismiss: (id: string) => void;
  onSaveEdit: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

function SuggestionItem({
  suggestion,
  busy,
  onConfirm,
  onReject,
  onDismiss,
  onSaveEdit,
  onDelete,
}: SuggestionItemProps) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(suggestion.title);

  return (
    <SuggestionCard
      title={
        editing ? (
          <input
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm focus:border-brand-500 focus:outline-none"
          />
        ) : (
          suggestion.title
        )
      }
      subtitle={`From ${suggestion.suggested_owner ?? "unknown sender"} · ${suggestion.ai_provider}/${suggestion.ai_model}`}
      evidenceText={suggestion.evidence_text}
      status={suggestion.status}
    >
      {suggestion.description ? (
        <p className="text-sm text-slate-600">{suggestion.description}</p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {suggestion.status === "SUGGESTED" ? (
          <>
            <button
              type="button"
              onClick={() => onConfirm(suggestion.id)}
              disabled={busy}
              className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-60"
            >
              Confirm
            </button>
            {editing ? (
              <button
                type="button"
                onClick={() => {
                  onSaveEdit(suggestion.id, title);
                  setEditing(false);
                }}
                disabled={busy}
                className="rounded-md border border-brand-300 px-3 py-1.5 text-sm font-medium text-brand-700 transition hover:bg-brand-50 disabled:opacity-60"
              >
                Save
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setEditing(true)}
                disabled={busy}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
              >
                Edit
              </button>
            )}
            <button
              type="button"
              onClick={() => onReject(suggestion.id)}
              disabled={busy}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
            >
              Reject
            </button>
            <button
              type="button"
              onClick={() => onDismiss(suggestion.id)}
              disabled={busy}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-500 transition hover:bg-slate-100 disabled:opacity-60"
            >
              Dismiss
            </button>
          </>
        ) : (
          <span className="text-xs text-slate-500">
            {suggestion.status === "CONFIRMED"
              ? "Confirmed into an action."
              : "No longer active."}
          </span>
        )}
        <button
          type="button"
          onClick={() => onDelete(suggestion.id)}
          disabled={busy}
          className="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 transition hover:bg-red-50 disabled:opacity-60"
        >
          Delete
        </button>
      </div>
    </SuggestionCard>
  );
}

export default function GmailSyncPage() {
  const { emailId } = useParams<{ emailId: string }>();
  const navigate = useNavigate();
  const [connections, setConnections] = useState<IntegrationConnection[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<EmailMessageRecord[]>([]);
  const [suggestions, setSuggestions] = useState<EmailTaskSuggestion[]>([]);
  const [lastRun, setLastRun] = useState<SyncRun | null>(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openedMessage, setOpenedMessage] =
    useState<EmailMessageRecord | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const connectionsRequestGeneration = useRef(0);
  const ingestedRequestGeneration = useRef(0);
  const detailRequestGeneration = useRef(0);

  const [signalPattern, setSignalPattern] = useState("");
  const [signalType, setSignalType] = useState<SenderSignalType>("PERSONAL");

  const gmailConnections = useMemo(
    () =>
      connections.filter(
        (connection) =>
          connection.service === "GMAIL" && connection.status === "CONNECTED",
      ),
    [connections],
  );

  const loadOpenedMessage = useCallback(async () => {
    const requestGeneration = ++detailRequestGeneration.current;
    if (!emailId) {
      setOpenedMessage(null);
      setDetailError(null);
      setDetailLoading(false);
      return;
    }
    setDetailLoading(true);
    setDetailError(null);
    try {
      const result = await gmailApi.getMessage(emailId);
      if (requestGeneration !== detailRequestGeneration.current) return;
      setOpenedMessage(result);
    } catch (err) {
      if (requestGeneration !== detailRequestGeneration.current) return;
      setOpenedMessage(null);
      setDetailError(
        err instanceof ApiError && err.status === 404
          ? "This ingested email could not be found."
          : "Could not load the opened email. Please retry.",
      );
    } finally {
      if (requestGeneration === detailRequestGeneration.current) {
        setDetailLoading(false);
      }
    }
  }, [emailId]);

  useEffect(() => {
    void loadOpenedMessage();
    return () => {
      detailRequestGeneration.current += 1;
    };
  }, [loadOpenedMessage]);

  const loadConnections = useCallback(async () => {
    const requestGeneration = ++connectionsRequestGeneration.current;
    setLoading(true);
    setError(null);
    try {
      const result = await integrationsApi.list();
      if (requestGeneration !== connectionsRequestGeneration.current) return;
      setConnections(result);
      const firstGmail = result.find(
        (connection) =>
          connection.service === "GMAIL" && connection.status === "CONNECTED",
      );
      setSelectedId((current) => current ?? firstGmail?.id ?? null);
    } catch {
      if (requestGeneration !== connectionsRequestGeneration.current) return;
      setError("Could not load your Gmail connections. Please retry.");
    } finally {
      if (requestGeneration === connectionsRequestGeneration.current) {
        setLoading(false);
      }
    }
  }, []);

  const loadIngested = useCallback(async () => {
    const requestGeneration = ++ingestedRequestGeneration.current;
    try {
      const [msgs, sugg] = await Promise.all([
        gmailApi.listMessages(),
        gmailApi.listSuggestions(),
      ]);
      if (requestGeneration !== ingestedRequestGeneration.current) return;
      setMessages(msgs);
      setSuggestions(sugg);
    } catch {
      if (requestGeneration !== ingestedRequestGeneration.current) return;
      setActionError("Could not load ingested email. Please retry.");
    }
  }, []);

  useEffect(() => {
    void loadConnections();
    return () => {
      connectionsRequestGeneration.current += 1;
    };
  }, [loadConnections]);

  useEffect(() => {
    void loadIngested();
    return () => {
      ingestedRequestGeneration.current += 1;
    };
  }, [loadIngested]);

  const handleInitialSync = useCallback(
    async (options: InitialSyncOptions) => {
      if (!selectedId) return;
      setBusy(true);
      setActionError(null);
      try {
        const run = await gmailApi.initialSync(selectedId, options);
        setLastRun(run);
        await loadIngested();
      } catch (err) {
        setActionError(describeError(err, "start the initial sync"));
      } finally {
        setBusy(false);
      }
    },
    [selectedId, loadIngested],
  );

  const handleSyncNow = useCallback(async () => {
    if (!selectedId) return;
    setBusy(true);
    setActionError(null);
    try {
      const run = await gmailApi.syncNow(selectedId);
      setLastRun(run);
      await loadIngested();
    } catch (err) {
      setActionError(describeError(err, "sync now"));
    } finally {
      setBusy(false);
    }
  }, [selectedId, loadIngested]);

  const runSuggestionAction = useCallback(
    async (fn: () => Promise<unknown>, verb: string) => {
      setBusy(true);
      setActionError(null);
      try {
        await fn();
        await loadIngested();
      } catch (err) {
        setActionError(describeError(err, verb));
      } finally {
        setBusy(false);
      }
    },
    [loadIngested],
  );

  const handleRemoveMessage = useCallback(
    async (record: EmailMessageRecord) => {
      if (
        !window.confirm(
          "Permanently delete this ingested message? Its extracted task suggestions are also deleted. This cannot be undone.",
        )
      ) {
        return;
      }

      await runSuggestionAction(
        () => gmailApi.removeMessage(record.id),
        "delete the message",
      );
      if (emailId === record.id) {
        setOpenedMessage(null);
        navigate("/gmail", { replace: true });
      }
    },
    [emailId, navigate, runSuggestionAction],
  );

  const handleMarkSignal = useCallback(async () => {
    const pattern = signalPattern.trim();
    if (!pattern) return;
    await runSuggestionAction(
      () => gmailApi.markSenderSignal({ pattern, signal_type: signalType }),
      "mark the sender",
    );
    setSignalPattern("");
  }, [signalPattern, signalType, runSuggestionAction]);

  if (loading) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8">
        <LoadingState variant="skeleton" rows={3} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-slate-900">Gmail sync</h1>
        <p className="mt-1 text-sm text-slate-500">
          Import email into your workspace. Messages flow through the same
          classify → confirm → knowledge pipeline; nothing becomes knowledge or
          a task without your confirmation.
        </p>
      </header>

      {emailId ? (
        <section className="mb-6 rounded-xl border border-brand-200 bg-brand-50/40 p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold text-slate-900">
              Opened email
            </h2>
            <Link
              to="/gmail"
              className="text-xs font-medium text-brand-600 hover:text-brand-700"
            >
              Close details
            </Link>
          </div>
          {detailLoading ? (
            <LoadingState label="Loading email..." />
          ) : detailError ? (
            <ErrorState
              message={detailError}
              onRetry={() => void loadOpenedMessage()}
            />
          ) : openedMessage ? (
            <ul>
              <EmailMessageCard
                record={openedMessage}
                busy={busy}
                onDelete={() => void handleRemoveMessage(openedMessage)}
              />
            </ul>
          ) : null}
        </section>
      ) : null}

      {error ? (
        <ErrorState message={error} onRetry={() => void loadConnections()} />
      ) : gmailConnections.length === 0 ? (
        <EmptyState
          variant="plain"
          title="No connected Gmail account."
          description="Connect Gmail on the Integrations page, then return here to sync."
        />
      ) : (
        <div className="space-y-6">
          {actionError ? (
            <ErrorState message={actionError} variant="alert" />
          ) : null}

          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <label
              htmlFor="gmail-connection"
              className="text-xs font-medium text-slate-600"
            >
              Connected account
            </label>
            <div className="mt-1 flex flex-wrap items-center gap-3">
              <select
                id="gmail-connection"
                value={selectedId ?? ""}
                onChange={(event) => setSelectedId(event.target.value)}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
              >
                {gmailConnections.map((connection) => (
                  <option key={connection.id} value={connection.id}>
                    {connection.account_email}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={handleSyncNow}
                disabled={busy || !selectedId}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
              >
                Sync now
              </button>
            </div>

            {lastRun ? (
              <p className="mt-3 text-xs text-slate-500">
                Last sync: {lastRun.records_created} new,{" "}
                {lastRun.skipped_duplicates} duplicates skipped,{" "}
                {lastRun.suggestions_created} task suggestion(s).
              </p>
            ) : null}
          </div>

          <InitialSyncForm disabled={busy || !selectedId} onSubmit={handleInitialSync} />

          {/* Sender/domain negative signal */}
          <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-900">
              Mark a sender or domain
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Marking a sender or domain personal/irrelevant is a negative signal
              future classification reuses to keep noise out of knowledge.
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input
                type="text"
                value={signalPattern}
                onChange={(event) => setSignalPattern(event.target.value)}
                placeholder="name@example.com or example.com"
                className="min-w-[16rem] flex-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
              />
              <select
                value={signalType}
                onChange={(event) =>
                  setSignalType(event.target.value as SenderSignalType)
                }
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
              >
                <option value="PERSONAL">Personal</option>
                <option value="IRRELEVANT">Irrelevant</option>
              </select>
              <button
                type="button"
                onClick={handleMarkSignal}
                disabled={busy || signalPattern.trim() === ""}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
              >
                Mark
              </button>
            </div>
          </div>

          {/* Task suggestions */}
          <section>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">
              Extracted task suggestions
            </h2>
            {suggestions.length === 0 ? (
              <EmptyState
                variant="plain"
                title="No task suggestions yet."
                description="Run a sync to extract task suggestions from your email."
              />
            ) : (
              <div className="space-y-3">
                {suggestions.map((suggestion) => (
                  <SuggestionItem
                    key={suggestion.id}
                    suggestion={suggestion}
                    busy={busy}
                    onConfirm={(id) =>
                      void runSuggestionAction(
                        () => gmailApi.confirmSuggestion(id),
                        "confirm the suggestion",
                      )
                    }
                    onReject={(id) =>
                      void runSuggestionAction(
                        () => gmailApi.rejectSuggestion(id),
                        "reject the suggestion",
                      )
                    }
                    onDismiss={(id) =>
                      void runSuggestionAction(
                        () => gmailApi.dismissSuggestion(id),
                        "dismiss the suggestion",
                      )
                    }
                    onSaveEdit={(id, title) =>
                      void runSuggestionAction(
                        () => gmailApi.editSuggestion(id, { title }),
                        "save the edit",
                      )
                    }
                    onDelete={(id) => {
                      if (
                        !window.confirm(
                          "Permanently delete this task suggestion? This cannot be undone.",
                        )
                      )
                        return;
                      void runSuggestionAction(
                        () => gmailApi.removeSuggestion(id),
                        "delete the suggestion",
                      );
                    }}
                  />
                ))}
              </div>
            )}
          </section>

          {/* Ingested messages */}
          <section>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">
              Ingested messages
            </h2>
            {messages.length === 0 ? (
              <EmptyState
                variant="plain"
                title="No ingested email yet."
                description="Configure and start an initial sync above."
              />
            ) : (
              <ul className="space-y-2">
                {messages.map((record) => (
                  <li
                    key={record.id}
                    className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-slate-900">
                          {record.subject || "(no subject)"}
                        </p>
                        <p className="mt-0.5 truncate text-xs text-slate-500">
                          {record.sender} · {formatDateTime(record.received_at)}
                        </p>
                      </div>
                      <div className="flex shrink-0 flex-col items-end gap-2">
                        <div className="flex flex-wrap justify-end gap-1">
                          {record.labels.map((label) => (
                            <span
                              key={label}
                              className="rounded bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500"
                            >
                              {label}
                            </span>
                          ))}
                          {record.has_attachments ? (
                            <span className="rounded bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                              attachment
                            </span>
                          ) : null}
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            if (
                              !window.confirm(
                                "Permanently delete this ingested message? Its extracted task suggestions are also deleted. This cannot be undone.",
                              )
                            )
                              return;
                            void runSuggestionAction(
                              () => gmailApi.removeMessage(record.id),
                              "delete the message",
                            );
                          }}
                          disabled={busy}
                          className="text-xs font-medium text-red-500 underline-offset-2 transition hover:text-red-700 hover:underline disabled:opacity-60"
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
