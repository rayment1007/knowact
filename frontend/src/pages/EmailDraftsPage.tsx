import { Link, useParams } from "react-router-dom";
// EmailDraftsPage: the AI-assisted Gmail draft workspace (M6.6, Requirement 32).
//
// Responsibilities:
//   - Request an AI draft grounded in permitted, confirmed context, choosing a
//     Gmail connection and a purpose/tone (`POST /api/email-drafts`).
//   - Review the suggested draft: subject, body, backend-resolved recipients,
//     the grounded referenced facts, and any warnings.
//   - Edit the draft, approve it (`/approve`) or reject it (`/reject`),
//     materialize a real Gmail draft (`/create-gmail-draft`), and — via a
//     DISTINCT explicit send-confirmation step (`/send`) separate from approval
//     — send it exactly once.
//
// The AI never invents recipients (they are backend-resolved and shown here),
// email is never sent automatically, and sending always requires the separate
// confirm gate. Loading/empty/error states reuse the shared feedback surfaces.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { peekCache } from "@/api/cache";
import { useQueryRefresh } from "@/hooks/useQueryRefresh";
import { ApiError, emailDraftsApi, integrationsApi } from "@/api";
import type {
  EmailDraft,
  EmailDraftStatus,
  IntegrationConnection,
  ReferencedFact,
} from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/feedback";

const STATUS_LABELS: Record<EmailDraftStatus, string> = {
  AI_SUGGESTED: "AI suggested",
  USER_APPROVED: "Approved",
  GMAIL_DRAFT_CREATED: "Gmail draft created",
  SENDING: "Sending",
  SENT: "Sent",
  REJECTED: "Rejected",
  FAILED: "Failed",
};

const STATUS_STYLES: Record<EmailDraftStatus, string> = {
  AI_SUGGESTED: "bg-slate-100 text-slate-700",
  USER_APPROVED: "bg-brand-100 text-brand-700",
  GMAIL_DRAFT_CREATED: "bg-indigo-100 text-indigo-700",
  SENDING: "bg-amber-100 text-amber-700",
  SENT: "bg-emerald-100 text-emerald-700",
  REJECTED: "bg-slate-200 text-slate-600",
  FAILED: "bg-red-100 text-red-700",
};

function describeError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "Your session expired. Please sign in again.";
    if (err.status === 403)
      return "Gmail compose access is required. Grant the gmail.compose scope from Integrations first.";
    if (err.status === 404) return "That draft could not be found.";
    if (err.status === 400)
      return "Sending needs an explicit confirmation.";
    return `${fallback} (${err.status}).`;
  }
  return fallback;
}

// The Gmail drafts folder. A per-draft deep link is not reliably derivable from
// the stored draft id, so we link to the user's drafts folder (Requirement 32).
const GMAIL_DRAFTS_URL = "https://mail.google.com/mail/u/0/#drafts";

// Parse a comma-separated recipient string into trimmed, non-empty addresses.
// Validation itself is the backend's job (Requirement 32.2); this only tidies
// the raw input before it is sent.
function parseRecipients(raw: string): string[] {
  return raw
    .split(",")
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
}

// Surface the backend's 422 recipient-validation message inline. FastAPI sends
// either a string `detail` (our explicit validation) or a list of field errors.
function recipientErrorMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 422) {
    const body = err.body as { detail?: unknown } | undefined;
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) {
      const first = body.detail[0] as { msg?: unknown } | undefined;
      if (first && typeof first.msg === "string") return first.msg;
    }
    return "That recipient address is not valid.";
  }
  return describeError(err, "Could not save the recipient");
}

function StatusBadge({ status }: { status: EmailDraftStatus }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${STATUS_STYLES[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

function ReferencedFactList({ facts }: { facts: ReferencedFact[] }) {
  if (facts.length === 0) {
    return (
      <p className="text-xs text-slate-400">
        No confirmed facts were referenced in this draft.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {facts.map((fact) => (
        <li
          key={`${fact.source_type}-${fact.source_id}`}
          className="rounded-md border border-slate-200 bg-slate-50 px-3 py-2"
        >
          <span className="rounded bg-brand-100 px-2 py-0.5 text-[11px] font-medium text-brand-700">
            {fact.source_type}
          </span>
          <p className="mt-1 line-clamp-3 text-xs text-slate-600">
            {fact.evidence}
          </p>
        </li>
      ))}
    </ul>
  );
}

interface DraftDetailProps {
  draft: EmailDraft;
  busy: boolean;
  actionError: string | null;
  recipientError: string | null;
  onEdit: (draft: EmailDraft, subject: string, bodyText: string) => void;
  onSaveRecipients: (draft: EmailDraft, recipients: string[]) => void;
  onApprove: (draft: EmailDraft) => void;
  onReject: (draft: EmailDraft) => void;
  onCreateGmailDraft: (draft: EmailDraft) => void;
  onSend: (draft: EmailDraft) => void;
  onDelete: (draft: EmailDraft) => void;
}

function DraftDetail({
  draft,
  busy,
  actionError,
  recipientError,
  onEdit,
  onSaveRecipients,
  onApprove,
  onReject,
  onCreateGmailDraft,
  onSend,
  onDelete,
}: DraftDetailProps) {
  const [subject, setSubject] = useState(draft.subject);
  const [bodyText, setBodyText] = useState(draft.body_text);
  // The user may type/override the recipient; the backend validates it.
  const [recipientInput, setRecipientInput] = useState(
    draft.to_recipients.join(", "),
  );
  // A distinct, explicit send-confirmation gate separate from approving content.
  const [confirmingSend, setConfirmingSend] = useState(false);

  // Reset local editor + confirm gate when a different draft is selected.
  useEffect(() => {
    setSubject(draft.subject);
    setBodyText(draft.body_text);
    setConfirmingSend(false);
  }, [draft.id, draft.subject, draft.body_text]);

  // Keep the recipient editor in sync with the persisted recipients (e.g. after
  // a successful save or when a different draft is selected).
  useEffect(() => {
    setRecipientInput(draft.to_recipients.join(", "));
  }, [draft.id, draft.to_recipients]);

  const editable =
    draft.status === "AI_SUGGESTED" || draft.status === "USER_APPROVED";
  // Recipients can be set until the draft is in-flight/terminal (matches the
  // backend edit guard).
  const recipientEditable =
    draft.status === "AI_SUGGESTED" ||
    draft.status === "USER_APPROVED" ||
    draft.status === "GMAIL_DRAFT_CREATED";
  const canApprove = draft.status === "AI_SUGGESTED";
  const canReject =
    draft.status === "AI_SUGGESTED" ||
    draft.status === "USER_APPROVED" ||
    draft.status === "GMAIL_DRAFT_CREATED";
  const canCreateGmailDraft = draft.status === "USER_APPROVED";
  // Sending stays blocked until a valid recipient is set (no valid recipient →
  // no send), keeping the "confirm before sending" guard intact.
  const canSend =
    (draft.status === "USER_APPROVED" ||
      draft.status === "GMAIL_DRAFT_CREATED") &&
    draft.to_recipients.length > 0;
  const dirty = subject !== draft.subject || bodyText !== draft.body_text;
  const recipientDirty = recipientInput.trim() !== draft.to_recipients.join(", ");
  // Show "Open in Gmail" once a real Gmail draft exists for this record.
  const gmailDraftUrl = draft.gmail_draft_id ? GMAIL_DRAFTS_URL : null;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <StatusBadge status={draft.status} />
            <span className="text-xs text-slate-400">
              {draft.tone} · {draft.purpose || "general"}
            </span>
          </div>
          <h2 className="mt-1 truncate text-lg font-semibold text-slate-900">
            {draft.subject || "(no subject)"}
          </h2>
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between gap-2">
          <label
            htmlFor={`recipient-${draft.id}`}
            className="text-xs font-semibold uppercase tracking-wider text-slate-400"
          >
            To (recipient)
          </label>
          {gmailDraftUrl ? (
            <a
              href={gmailDraftUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-medium text-indigo-600 transition hover:text-indigo-700 hover:underline"
            >
              Open in Gmail ↗
            </a>
          ) : null}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <input
            id={`recipient-${draft.id}`}
            type="text"
            value={recipientInput}
            disabled={!recipientEditable || busy}
            onChange={(event) => setRecipientInput(event.target.value)}
            placeholder="name@example.com"
            className="min-w-[240px] flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none disabled:bg-slate-50"
          />
          {recipientEditable ? (
            <button
              type="button"
              disabled={busy || !recipientDirty}
              onClick={() =>
                onSaveRecipients(draft, parseRecipients(recipientInput))
              }
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Save recipient
            </button>
          ) : null}
        </div>
        <p className="mt-1 text-[11px] text-slate-400">
          You can type a recipient; the system validates the address before it's
          used. Separate multiple addresses with commas.
        </p>
        {recipientError ? (
          <p className="mt-1 text-xs text-red-600">{recipientError}</p>
        ) : draft.to_recipients.length === 0 ? (
          <p className="mt-1 text-sm text-amber-600">
            No recipient resolved yet — confirm before sending.
          </p>
        ) : null}
      </div>

      {draft.warnings.length > 0 ? (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-amber-700">
            Warnings
          </div>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-amber-700">
            {draft.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="space-y-3">
        <label className="block">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Subject
          </span>
          <input
            type="text"
            value={subject}
            disabled={!editable || busy}
            onChange={(event) => setSubject(event.target.value)}
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none disabled:bg-slate-50"
          />
        </label>
        <label className="block">
          <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Body
          </span>
          <textarea
            value={bodyText}
            disabled={!editable || busy}
            onChange={(event) => setBodyText(event.target.value)}
            rows={10}
            className="mt-1 w-full resize-y rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none disabled:bg-slate-50"
          />
        </label>
        {editable ? (
          <button
            type="button"
            disabled={!dirty || busy}
            onClick={() => onEdit(draft, subject, bodyText)}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            Save edits
          </button>
        ) : null}
      </div>

      <div>
        <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Referenced facts
        </div>
        <div className="mt-2">
          <ReferencedFactList facts={draft.referenced_facts} />
        </div>
      </div>

      {actionError ? <ErrorState message={actionError} variant="alert" /> : null}

      <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-4">
        <button
          type="button"
          disabled={!canApprove || busy}
          onClick={() => onApprove(draft)}
          className="rounded-md bg-brand-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Approve
        </button>
        <button
          type="button"
          disabled={!canCreateGmailDraft || busy}
          onClick={() => onCreateGmailDraft(draft)}
          className="rounded-md border border-indigo-300 px-3 py-1.5 text-sm font-medium text-indigo-700 transition hover:bg-indigo-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Create Gmail draft
        </button>
        <button
          type="button"
          disabled={!canReject || busy}
          onClick={() => onReject(draft)}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 transition hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Reject
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onDelete(draft)}
          className="rounded-md border border-red-200 px-3 py-1.5 text-sm font-medium text-red-600 transition hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          Delete
        </button>

        <div className="ml-auto">
          {draft.status === "SENT" ? (
            <span className="text-sm font-medium text-emerald-700">
              Sent · {draft.gmail_sent_message_id}
            </span>
          ) : !confirmingSend ? (
            <button
              type="button"
              disabled={!canSend || busy}
              onClick={() => setConfirmingSend(true)}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              Send…
            </button>
          ) : (
            <div className="flex items-center gap-2 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-2">
              <span className="text-xs font-medium text-emerald-800">
                Send this email now? This is a separate, final confirmation.
              </span>
              <button
                type="button"
                disabled={busy}
                onClick={() => onSend(draft)}
                className="rounded-md bg-emerald-600 px-3 py-1 text-xs font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-60"
              >
                {busy ? "Sending…" : "Confirm & send"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setConfirmingSend(false)}
                className="rounded-md px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function EmailDraftsPage() {
  const { draftId } = useParams();
  const [drafts, setDrafts] = useState<EmailDraft[]>(() => peekCache<EmailDraft[]>("/email-drafts") ?? []);
  const [connections, setConnections] = useState<IntegrationConnection[]>(() => peekCache<IntegrationConnection[]>("/integrations") ?? []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(() => !peekCache("/email-drafts"));
  const loaded = useRef(!!peekCache("/email-drafts"));
  const requestGeneration = useRef(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [recipientError, setRecipientError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Request-draft form state.
  const [connectionId, setConnectionId] = useState("");
  const [purpose, setPurpose] = useState("");
  const [tone, setTone] = useState("professional");
  const [requesting, setRequesting] = useState(false);

  const gmailConnections = useMemo(
    () => connections.filter((c) => c.service === "GMAIL"),
    [connections],
  );

  const selected = useMemo(
    () => drafts.find((d) => d.id === selectedId) ?? null,
    [drafts, selectedId],
  );

  const load = useCallback(async () => {
    const generation = ++requestGeneration.current;
    setLoading(!loaded.current);
    setLoadError(null);
    try {
      const [draftList, connectionList] = await Promise.all([
        emailDraftsApi.list(),
        integrationsApi.list(),
      ]);
      if (generation !== requestGeneration.current) return;
      setDrafts(draftList);
      loaded.current = true;
      setConnections(connectionList);
      setSelectedId(current => draftId ?? (draftList.some(draft => draft.id === current) ? current : draftList[0]?.id ?? null));
    } catch (err) {
      if (generation !== requestGeneration.current) return;
      setLoadError(describeError(err, "Could not load drafts"));
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }, [draftId]);

  useQueryRefresh("/email-drafts", load);
  useQueryRefresh("/integrations", load);

  useEffect(() => {
    void load();
    return () => { requestGeneration.current++; };
  }, [load]);

  useEffect(() => {
    if (!connectionId && gmailConnections[0]) {
      setConnectionId(gmailConnections[0].id);
    }
  }, [connectionId, gmailConnections]);

  const upsertDraft = useCallback((updated: EmailDraft) => {
    setDrafts((prev) => {
      const exists = prev.some((d) => d.id === updated.id);
      return exists
        ? prev.map((d) => (d.id === updated.id ? updated : d))
        : [updated, ...prev];
    });
    setSelectedId(updated.id);
  }, []);

  const runAction = useCallback(
    async (fn: () => Promise<EmailDraft>, fallback: string) => {
      if (busy) return;
      setBusy(true);
      setActionError(null);
      try {
        upsertDraft(await fn());
      } catch (err) {
        setActionError(describeError(err, fallback));
      } finally {
        setBusy(false);
      }
    },
    [busy, upsertDraft],
  );

  // Saving recipients surfaces the backend's 422 validation message inline
  // (distinct from the lifecycle action error) and clears it on success.
  const saveRecipients = useCallback(
    async (draft: EmailDraft, recipients: string[]) => {
      if (busy) return;
      setBusy(true);
      setRecipientError(null);
      setActionError(null);
      try {
        upsertDraft(
          await emailDraftsApi.edit(draft.id, { to_recipients: recipients }),
        );
      } catch (err) {
        setRecipientError(recipientErrorMessage(err));
      } finally {
        setBusy(false);
      }
    },
    [busy, upsertDraft],
  );

  // Clear a stale recipient error when a different draft is selected.
  useEffect(() => {
    setRecipientError(null);
  }, [selectedId]);

  const deleteDraft = useCallback(
    async (draft: EmailDraft) => {
      if (busy) return;
      const confirmed = window.confirm(
        "Permanently delete this local draft? This only removes it here and never touches Gmail. This cannot be undone.",
      );
      if (!confirmed) return;
      setBusy(true);
      setActionError(null);
      try {
        await emailDraftsApi.remove(draft.id);
        setDrafts((prev) => {
          const next = prev.filter((d) => d.id !== draft.id);
          setSelectedId((current) =>
            current === draft.id ? next[0]?.id ?? null : current,
          );
          return next;
        });
      } catch (err) {
        setActionError(describeError(err, "Could not delete the draft"));
      } finally {
        setBusy(false);
      }
    },
    [busy],
  );

  const requestDraft = useCallback(async () => {
    if (!connectionId || requesting) return;
    setRequesting(true);
    setActionError(null);
    try {
      const draft = await emailDraftsApi.request({
        connection_id: connectionId,
        purpose,
        tone,
      });
      upsertDraft(draft);
      setPurpose("");
    } catch (err) {
      setActionError(describeError(err, "Could not request a draft"));
    } finally {
      setRequesting(false);
    }
  }, [connectionId, purpose, tone, requesting, upsertDraft]);

  return (
    <div className="mx-auto flex h-full max-w-6xl flex-col px-6 py-8">
      <header className="mb-4">
        <Link to="/workspace/actions" className="mb-3 inline-block text-sm font-medium text-blue-700 hover:underline">? Workspace actions</Link>
        <h1 className="text-xl font-semibold text-slate-900">Gmail AI Drafts</h1>
        <p className="mt-1 text-sm text-slate-500">
          Draft grounded Gmail replies from your confirmed context. Recipients
          are resolved by the system, and email is only ever sent after a
          separate, explicit confirmation.
        </p>
      </header>

      <section className="mb-6 rounded-lg border border-slate-200 bg-white p-4">
        <div className="text-sm font-semibold text-slate-800">
          Request an AI draft
        </div>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <label className="flex flex-col">
            <span className="text-xs font-medium text-slate-500">
              Gmail connection
            </span>
            <select
              value={connectionId}
              onChange={(event) => setConnectionId(event.target.value)}
              disabled={gmailConnections.length === 0 || requesting}
              className="mt-1 min-w-[220px] rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none disabled:bg-slate-50"
            >
              {gmailConnections.length === 0 ? (
                <option value="">No Gmail connection</option>
              ) : (
                gmailConnections.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.account_email}
                  </option>
                ))
              )}
            </select>
          </label>
          <label className="flex flex-1 flex-col">
            <span className="text-xs font-medium text-slate-500">Purpose</span>
            <input
              type="text"
              value={purpose}
              onChange={(event) => setPurpose(event.target.value)}
              placeholder="e.g. follow up on the proposal"
              disabled={requesting}
              className="mt-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
            />
          </label>
          <label className="flex flex-col">
            <span className="text-xs font-medium text-slate-500">Tone</span>
            <select
              value={tone}
              onChange={(event) => setTone(event.target.value)}
              disabled={requesting}
              className="mt-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
            >
              <option value="professional">Professional</option>
              <option value="friendly">Friendly</option>
              <option value="concise">Concise</option>
              <option value="formal">Formal</option>
            </select>
          </label>
          <button
            type="button"
            onClick={() => void requestDraft()}
            disabled={!connectionId || requesting}
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {requesting ? "Drafting…" : "Draft with AI"}
          </button>
        </div>
        {gmailConnections.length === 0 ? (
          <p className="mt-2 text-xs text-amber-600">
            Connect Gmail with the compose scope from Integrations to draft
            replies.
          </p>
        ) : null}
      </section>

      {loading ? (
        <LoadingState variant="skeleton" rows={3} />
      ) : loadError ? (
        <ErrorState message={loadError} onRetry={() => void load()} />
      ) : drafts.length === 0 ? (
        <EmptyState
          title="No drafts yet."
          description="Request an AI draft above to get started."
        />
      ) : (
        <div className="grid flex-1 grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
          <aside className="space-y-2">
            {drafts.map((draft) => (
              <button
                key={draft.id}
                type="button"
                onClick={() => setSelectedId(draft.id)}
                className={`w-full rounded-lg border px-3 py-2 text-left transition ${
                  draft.id === selectedId
                    ? "border-brand-400 bg-brand-50"
                    : "border-slate-200 bg-white hover:bg-slate-50"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-sm font-medium text-slate-800">
                    {draft.subject || "(no subject)"}
                  </span>
                  <StatusBadge status={draft.status} />
                </div>
                <p className="mt-1 truncate text-xs text-slate-500">
                  {draft.to_recipients.join(", ") || "no recipient"}
                </p>
              </button>
            ))}
          </aside>

          <section className="rounded-lg border border-slate-200 bg-white p-5">
            {selected ? (
              <DraftDetail
                draft={selected}
                busy={busy}
                actionError={actionError}
                recipientError={recipientError}
                onEdit={(draft, subject, bodyText) =>
                  void runAction(
                    () =>
                      emailDraftsApi.edit(draft.id, {
                        subject,
                        body_text: bodyText,
                      }),
                    "Could not save edits",
                  )
                }
                onSaveRecipients={(draft, recipients) =>
                  void saveRecipients(draft, recipients)
                }
                onApprove={(draft) =>
                  void runAction(
                    () => emailDraftsApi.approve(draft.id),
                    "Could not approve the draft",
                  )
                }
                onReject={(draft) =>
                  void runAction(
                    () => emailDraftsApi.reject(draft.id),
                    "Could not reject the draft",
                  )
                }
                onCreateGmailDraft={(draft) =>
                  void runAction(
                    () => emailDraftsApi.createGmailDraft(draft.id),
                    "Could not create the Gmail draft",
                  )
                }
                onSend={(draft) =>
                  void runAction(
                    () => emailDraftsApi.send(draft.id, true),
                    "Could not send the email",
                  )
                }
                onDelete={(draft) => void deleteDraft(draft)}
              />
            ) : (
              <EmptyState title="Select a draft to review." variant="plain" />
            )}
          </section>
        </div>
      )}
    </div>
  );
}
