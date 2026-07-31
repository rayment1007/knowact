// Typed API wrappers for AI-assisted Gmail drafts (M6.6, Requirement 32).
//
// Every call is cookie-authenticated by the shared `api` client and scoped
// server-side to the caller's organization and user, so a cross-org draft id is
// indistinguishable from a missing one and yields ApiError(404). Responses are
// token-safe: no token or secret field is ever present.
//
// The lifecycle is human-in-the-loop: the AI produces a SUGGESTED draft, the
// user edits/approves it, materializes a Gmail draft, and — via a SEPARATE
// explicit confirmation distinct from approval — sends it exactly once. Email
// is never sent automatically, and a retried/concurrent send never produces a
// duplicate (Property 17).

import { api } from "./client";
import type {
  EmailDraft,
  EmailDraftEdit,
  EmailDraftRequest,
} from "./types";

export const emailDraftsApi = {
  /** Request an AI draft grounded in permitted context (`AI_SUGGESTED`). */
  request: (payload: EmailDraftRequest) =>
    api.post<EmailDraft>("/email-drafts", payload),

  /** List the current user's drafts, newest first. */
  list: () => api.get<EmailDraft[]>("/email-drafts"),

  /** Fetch a single draft; a cross-org/missing id yields ApiError(404). */
  get: (draftId: string) => api.get<EmailDraft>(`/email-drafts/${draftId}`),

  /** Edit draft content before it is sent (Requirement 32.5). */
  edit: (draftId: string, edit: EmailDraftEdit) =>
    api.patch<EmailDraft>(`/email-drafts/${draftId}`, edit),

  /** Approve draft content → `USER_APPROVED` (Requirement 32.5). */
  approve: (draftId: string) =>
    api.post<EmailDraft>(`/email-drafts/${draftId}/approve`),

  /** Reject the draft → `REJECTED` (Requirement 32.5). */
  reject: (draftId: string) =>
    api.post<EmailDraft>(`/email-drafts/${draftId}/reject`),

  /**
   * Create a real Gmail draft from an approved draft (Requirement 32.6).
   * Requires the incremental `gmail.compose` scope; without it, ApiError(403).
   */
  createGmailDraft: (draftId: string) =>
    api.post<EmailDraft>(`/email-drafts/${draftId}/create-gmail-draft`),

  /**
   * Send the draft after a SEPARATE explicit confirmation (Requirement 32.7,
   * 32.8). `confirm` must be `true`; the send is idempotent under retry.
   */
  send: (draftId: string, confirm: boolean) =>
    api.post<EmailDraft>(`/email-drafts/${draftId}/send`, { confirm }),

  /**
   * Permanently delete the LOCAL draft only — this never calls Gmail, so any
   * materialized Gmail draft or sent message is untouched. A cross-org/missing
   * id yields ApiError(404).
   */
  remove: (draftId: string) => api.del<void>(`/email-drafts/${draftId}`),
};
