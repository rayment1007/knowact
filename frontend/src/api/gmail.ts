// Typed API wrappers for Connected Workspace Intelligence Gmail sync &
// ingestion (Requirements 26, 27).
//
// Every call is cookie-authenticated by the shared `api` client and scoped
// server-side to the caller's organization and user. Responses are token-safe:
// no token or key field is ever present. Cross-org connection/record ids yield
// ApiError(404).

import { api } from "./client";
import type {
  EmailMessageRecord,
  EmailTaskSuggestion,
  EmailTaskSuggestionEdit,
  InitialSyncOptions,
  SenderSignal,
  SenderSignalRequest,
  SyncRun,
} from "./types";

export const gmailApi = {
  /**
   * Start the first Gmail import for a connection with the chosen options
   * (Requirements 26.1-26.6). An invalid options payload yields ApiError(422)
   * and starts no partial sync.
   */
  initialSync: (connectionId: string, options: InitialSyncOptions) =>
    api.post<SyncRun>(`/gmail/${connectionId}/initial-sync`, options),

  /** Run a manual incremental sync; dedup keeps it idempotent (Req 27.8). */
  syncNow: (connectionId: string) =>
    api.post<SyncRun>(`/gmail/${connectionId}/sync-now`),

  /** List ingested email provenance records, newest first (Requirement 27.9). */
  listMessages: (connectionId?: string) => {
    const query = connectionId
      ? `?${new URLSearchParams({ connection_id: connectionId }).toString()}`
      : "";
    return api.get<EmailMessageRecord[]>(`/gmail/messages${query}`);
  },

  /** List extracted task suggestions (Requirement 27.6). */
  listSuggestions: (recordId?: string) => {
    const query = recordId
      ? `?${new URLSearchParams({ record_id: recordId }).toString()}`
      : "";
    return api.get<EmailTaskSuggestion[]>(`/gmail/suggestions${query}`);
  },

  /** Confirm a task suggestion into an OPEN action (Requirement 27.7). */
  confirmSuggestion: (suggestionId: string) =>
    api.post<EmailTaskSuggestion>(`/gmail/suggestions/${suggestionId}/confirm`),

  /** Apply a partial edit to a task suggestion (Requirement 27.7). */
  editSuggestion: (suggestionId: string, edit: EmailTaskSuggestionEdit) =>
    api.patch<EmailTaskSuggestion>(`/gmail/suggestions/${suggestionId}`, edit),

  /** Reject a task suggestion (retained as a negative signal) (Req 27.7). */
  rejectSuggestion: (suggestionId: string) =>
    api.post<EmailTaskSuggestion>(`/gmail/suggestions/${suggestionId}/reject`),

  /** Dismiss a task suggestion (Requirement 27.7). */
  dismissSuggestion: (suggestionId: string) =>
    api.post<EmailTaskSuggestion>(`/gmail/suggestions/${suggestionId}/dismiss`),

  /**
   * Permanently delete an ingested email record; also cascades (deletes) the
   * record's task suggestions. Cross-org/missing id yields ApiError(404).
   */
  removeMessage: (recordId: string) =>
    api.del<void>(`/gmail/messages/${recordId}`),

  /**
   * Permanently delete an extracted task suggestion. Cross-org/missing id
   * yields ApiError(404).
   */
  removeSuggestion: (suggestionId: string) =>
    api.del<void>(`/gmail/suggestions/${suggestionId}`),

  /** Mark a sender address or domain personal/irrelevant (Requirement 27.7). */
  markSenderSignal: (request: SenderSignalRequest) =>
    api.post<SenderSignal>("/gmail/sender-signals", request),
};
