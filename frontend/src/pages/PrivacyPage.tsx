// PrivacyPage: privacy, control & deletion (M6.7, Requirement 33).
//
// Responsibilities:
//   - Show each connection's last-sync status (last sync time, last error,
//     status) and let the user disconnect (stop future sync, retain data) or
//     revoke Google access (Requirements 33.1, 33.2, 33.6).
//   - Delete imported email data by scope (all or one connection), cascading
//     derived chunks/embeddings and retaining confirmed records (33.3, 33.8).
//   - Delete uploaded documents, removing the binary + chunks + embeddings (33.4).
//   - Choose the raw-email retention policy (RAW_AND_EXTRACTED vs
//     EXTRACTED_ONLY, plus an optional window) (33.5).
//   - See exactly what data grounded an AI answer, by answer id (33.7).
//
// Loading / empty / error surfaces reuse the shared feedback components. No
// token or secret is ever displayed — the API views never carry one.

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  documentsApi,
  integrationsApi,
  privacyApi,
} from "@/api";
import type {
  AnswerProvenance,
  DocumentAsset,
  EmailDataDeleteScope,
  RawEmailRetentionMode,
  SyncStatus,
} from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/feedback";

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
}

function describeError(err: unknown, verb: string): string {
  if (err instanceof ApiError) {
    if (err.status === 404) return "That item no longer exists. Refreshed.";
    return `Could not ${verb} (${err.status}). Please retry.`;
  }
  return `Could not ${verb}. Please retry.`;
}

const STATUS_STYLES: Record<string, string> = {
  CONNECTED: "bg-emerald-50 text-emerald-700",
  EXPIRED: "bg-amber-50 text-amber-700",
  REVOKED: "bg-slate-100 text-slate-600",
  ERROR: "bg-rose-50 text-rose-700",
};

const RETENTION_MODES: { value: RawEmailRetentionMode; label: string }[] = [
  { value: "EXTRACTED_ONLY", label: "Extracted text only (drop raw content)" },
  { value: "RAW_AND_EXTRACTED", label: "Keep raw and extracted content" },
];

export default function PrivacyPage() {
  const [connections, setConnections] = useState<SyncStatus[]>([]);
  const [documents, setDocuments] = useState<DocumentAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Delete-email-data controls.
  const [deleteScope, setDeleteScope] = useState<EmailDataDeleteScope>("ALL");
  const [deleteConnectionId, setDeleteConnectionId] = useState<string>("");

  // Retention-policy controls.
  const [retentionMode, setRetentionMode] =
    useState<RawEmailRetentionMode>("EXTRACTED_ONLY");
  const [retentionWindow, setRetentionWindow] = useState<string>("");

  // Answer-provenance controls.
  const [answerId, setAnswerId] = useState<string>("");
  const [provenance, setProvenance] = useState<AnswerProvenance | null>(null);
  const [provenanceError, setProvenanceError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [status, docs] = await Promise.all([
        privacyApi.syncStatus(),
        documentsApi.list(),
      ]);
      setConnections(status);
      setDocuments(docs);
    } catch {
      setError("Could not load your privacy settings. Please retry.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runAction = useCallback(
    async (fn: () => Promise<unknown>, verb: string, ok: string) => {
      setBusy(true);
      setActionError(null);
      setNotice(null);
      try {
        await fn();
        setNotice(ok);
        await load();
      } catch (err) {
        setActionError(describeError(err, verb));
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  const handleDeleteEmailData = useCallback(() => {
    if (deleteScope === "CONNECTION" && !deleteConnectionId) {
      setActionError("Choose a connection to delete its email data.");
      return;
    }
    const scopeLabel =
      deleteScope === "ALL" ? "all imported email data" : "this connection's email data";
    if (
      !window.confirm(
        `Delete ${scopeLabel}? Related suggestions and derived embeddings are removed; confirmed records are retained.`,
      )
    ) {
      return;
    }
    void runAction(
      () =>
        privacyApi.deleteEmailData({
          scope: deleteScope,
          connection_id:
            deleteScope === "CONNECTION" ? deleteConnectionId : undefined,
        }),
      "delete email data",
      "Imported email data deleted.",
    );
  }, [deleteScope, deleteConnectionId, runAction]);

  const handleSaveRetention = useCallback(() => {
    const window_days = retentionWindow.trim()
      ? Number.parseInt(retentionWindow, 10)
      : undefined;
    void runAction(
      () =>
        privacyApi.setRetentionPolicy({
          mode: retentionMode,
          retention_window_days:
            window_days && !Number.isNaN(window_days) ? window_days : undefined,
        }),
      "save the retention policy",
      "Retention policy saved.",
    );
  }, [retentionMode, retentionWindow, runAction]);

  const handleFetchProvenance = useCallback(async () => {
    const trimmed = answerId.trim();
    if (!trimmed) return;
    setBusy(true);
    setProvenanceError(null);
    setProvenance(null);
    try {
      setProvenance(await privacyApi.answerProvenance(trimmed));
    } catch (err) {
      setProvenanceError(describeError(err, "load the answer provenance"));
    } finally {
      setBusy(false);
    }
  }, [answerId]);

  if (loading) {
    return (
      <div className="mx-auto max-w-4xl px-6 py-8">
        <LoadingState variant="skeleton" rows={4} />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header className="mb-6">
        <h1 className="text-xl font-semibold text-slate-900">
          Privacy &amp; data control
        </h1>
        <p className="mt-1 text-sm text-slate-500">
          Disconnect or revoke connected services, delete imported data, choose
          how long raw email is kept, and see exactly what data grounded an AI
          answer. You stay in full control of your connected information.
        </p>
      </header>

      {error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <div className="space-y-8">
          {actionError ? (
            <ErrorState message={actionError} variant="alert" />
          ) : null}
          {notice ? (
            <div
              role="status"
              className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700"
            >
              {notice}
            </div>
          ) : null}

          {/* Connections & last-sync status ------------------------------- */}
          <section>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">
              Connections &amp; sync status
            </h2>
            {connections.length === 0 ? (
              <EmptyState
                variant="plain"
                title="No connected services."
                description="Connect Gmail or Calendar from the Integrations page."
              />
            ) : (
              <ul className="space-y-2">
                {connections.map((c) => (
                  <li
                    key={c.connection_id}
                    className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-slate-900">
                          {c.service} · {c.account_email}
                        </p>
                        <p className="mt-0.5 text-xs text-slate-500">
                          Last sync: {formatDateTime(c.last_sync_at)}
                        </p>
                        {c.last_error ? (
                          <p className="mt-0.5 text-xs text-rose-600">
                            Last error: {c.last_error}
                          </p>
                        ) : null}
                      </div>
                      <span
                        className={`shrink-0 rounded px-2 py-0.5 text-[11px] font-medium ${
                          STATUS_STYLES[c.status] ?? "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {c.status}
                      </span>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          void runAction(
                            () => integrationsApi.disconnect(c.connection_id),
                            "disconnect the service",
                            "Disconnected. Future sync stopped; imported data retained.",
                          )
                        }
                        disabled={busy || c.status === "REVOKED"}
                        className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:opacity-60"
                      >
                        Disconnect
                      </button>
                      <button
                        type="button"
                        onClick={() => {
                          if (
                            !window.confirm(
                              `Revoke Google access for ${c.account_email}? Sync will stop and reconnecting will require authorization again.`,
                            )
                          ) {
                            return;
                          }
                          void runAction(
                            () => integrationsApi.revoke(c.connection_id),
                            "revoke Google access",
                            "Google access revoked and disconnected locally.",
                          );
                        }}
                        disabled={busy}
                        className="rounded-md border border-rose-300 px-3 py-1.5 text-sm font-medium text-rose-700 transition hover:bg-rose-50 disabled:opacity-60"
                      >
                        Revoke access
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Delete imported email data ----------------------------------- */}
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-900">
              Delete imported email data
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Removes the selected email records and any derived embeddings and
              retrieval entries. Confirmed business records are retained with a
              note that their source was deleted.
            </p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <div>
                <label
                  htmlFor="delete-scope"
                  className="text-xs font-medium text-slate-600"
                >
                  Scope
                </label>
                <select
                  id="delete-scope"
                  value={deleteScope}
                  onChange={(e) =>
                    setDeleteScope(e.target.value as EmailDataDeleteScope)
                  }
                  className="mt-1 block rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
                >
                  <option value="ALL">All imported email</option>
                  <option value="CONNECTION">One connection</option>
                </select>
              </div>
              {deleteScope === "CONNECTION" ? (
                <div>
                  <label
                    htmlFor="delete-connection"
                    className="text-xs font-medium text-slate-600"
                  >
                    Connection
                  </label>
                  <select
                    id="delete-connection"
                    value={deleteConnectionId}
                    onChange={(e) => setDeleteConnectionId(e.target.value)}
                    className="mt-1 block rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
                  >
                    <option value="">Select…</option>
                    {connections.map((c) => (
                      <option key={c.connection_id} value={c.connection_id}>
                        {c.service} · {c.account_email}
                      </option>
                    ))}
                  </select>
                </div>
              ) : null}
              <button
                type="button"
                onClick={handleDeleteEmailData}
                disabled={busy}
                className="rounded-md border border-rose-300 px-4 py-2 text-sm font-medium text-rose-700 transition hover:bg-rose-50 disabled:opacity-60"
              >
                Delete email data
              </button>
            </div>
          </section>

          {/* Raw-email retention policy ----------------------------------- */}
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-900">
              Raw-email retention
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Choose whether raw message content is kept alongside the extracted
              text. Choosing “extracted only” drops retained raw content now.
            </p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <div>
                <label
                  htmlFor="retention-mode"
                  className="text-xs font-medium text-slate-600"
                >
                  Policy
                </label>
                <select
                  id="retention-mode"
                  value={retentionMode}
                  onChange={(e) =>
                    setRetentionMode(e.target.value as RawEmailRetentionMode)
                  }
                  className="mt-1 block rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
                >
                  {RETENTION_MODES.map((m) => (
                    <option key={m.value} value={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label
                  htmlFor="retention-window"
                  className="text-xs font-medium text-slate-600"
                >
                  Retention window (days, optional)
                </label>
                <input
                  id="retention-window"
                  type="number"
                  min={1}
                  max={3650}
                  value={retentionWindow}
                  onChange={(e) => setRetentionWindow(e.target.value)}
                  className="mt-1 block w-48 rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
                  placeholder="e.g. 90"
                />
              </div>
              <button
                type="button"
                onClick={handleSaveRetention}
                disabled={busy}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-60"
              >
                Save policy
              </button>
            </div>
          </section>

          {/* Delete uploaded documents ------------------------------------ */}
          <section>
            <h2 className="mb-3 text-sm font-semibold text-slate-900">
              Uploaded documents
            </h2>
            {documents.length === 0 ? (
              <EmptyState
                variant="plain"
                title="No uploaded documents."
                description="Upload documents from the Documents page."
              />
            ) : (
              <ul className="space-y-2">
                {documents.map((doc) => (
                  <li
                    key={doc.id}
                    className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-900">
                        {doc.filename}
                        {doc.source_deleted ? (
                          <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-medium text-slate-500">
                            source deleted
                          </span>
                        ) : null}
                      </p>
                      <p className="mt-0.5 truncate text-xs text-slate-500">
                        {doc.mime_type} · {doc.sensitivity} ·{" "}
                        {formatDateTime(doc.created_at)}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        if (
                          !window.confirm(
                            `Delete “${doc.filename}”? This removes the file and its embeddings.`,
                          )
                        ) {
                          return;
                        }
                        void runAction(
                          () => privacyApi.deleteDocument(doc.id),
                          "delete the document",
                          "Document deleted.",
                        );
                      }}
                      disabled={busy}
                      className="shrink-0 rounded-md border border-rose-300 px-3 py-1.5 text-sm font-medium text-rose-700 transition hover:bg-rose-50 disabled:opacity-60"
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* What data was used for an AI answer -------------------------- */}
          <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-900">
              What data was used for an AI answer
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Paste an answer id (returned by the Copilot) to see the exact
              citations and evidence set that grounded it.
            </p>
            <div className="mt-4 flex flex-wrap items-end gap-3">
              <div className="min-w-0 flex-1">
                <label
                  htmlFor="answer-id"
                  className="text-xs font-medium text-slate-600"
                >
                  Answer id
                </label>
                <input
                  id="answer-id"
                  type="text"
                  value={answerId}
                  onChange={(e) => setAnswerId(e.target.value)}
                  className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
                  placeholder="answer id"
                />
              </div>
              <button
                type="button"
                onClick={() => void handleFetchProvenance()}
                disabled={busy || !answerId.trim()}
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:opacity-60"
              >
                Show data used
              </button>
            </div>

            {provenanceError ? (
              <div className="mt-3">
                <ErrorState message={provenanceError} variant="alert" />
              </div>
            ) : null}

            {provenance ? (
              <div className="mt-4 space-y-3">
                <div>
                  <p className="text-xs font-medium text-slate-600">Question</p>
                  <p className="text-sm text-slate-900">{provenance.question}</p>
                </div>
                {provenance.answer ? (
                  <div>
                    <p className="text-xs font-medium text-slate-600">Answer</p>
                    <p className="text-sm text-slate-900">{provenance.answer}</p>
                  </div>
                ) : null}
                <div>
                  <p className="text-xs font-medium text-slate-600">
                    Evidence used ({provenance.evidence.length})
                  </p>
                  {provenance.evidence.length === 0 ? (
                    <p className="text-xs text-slate-400">
                      No confirmed evidence grounded this answer.
                    </p>
                  ) : (
                    <ul className="mt-1 space-y-2">
                      {provenance.evidence.map((e) => (
                        <li
                          key={`${e.source_type}-${e.source_id}`}
                          className="rounded-md border border-slate-200 bg-slate-50 p-3"
                        >
                          <p className="text-xs font-medium text-slate-700">
                            {e.source_type} · {e.title}
                          </p>
                          <p className="mt-0.5 text-xs text-slate-500">
                            {e.evidence_excerpt}
                          </p>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            ) : null}
          </section>
        </div>
      )}
    </div>
  );
}
