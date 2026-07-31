// Typed API wrappers for Privacy, Control & Deletion (M6.7, Requirement 33).
//
// Every call is cookie-authenticated by the shared `api` client and scoped
// server-side to the caller's organization. Disconnect/revoke reuse the
// integrations endpoints (they already stop future sync / revoke the Google
// grant and write one audit row each); the privacy endpoints here add data
// deletion, retention control, last-sync status, and answer provenance. No
// token or secret is ever present in any response.

import { api, apiRequest } from "./client";
import type {
  AnswerProvenance,
  EmailDataDeleteRequest,
  EmailDataDeleteResponse,
  RetentionPolicy,
  RetentionPolicyUpdate,
  SyncStatus,
} from "./types";

export const privacyApi = {
  /**
   * Delete imported email data (Requirement 33.3). Removes the selected email
   * records and, in the same transaction, their dependent suggestions, any
   * derived chunks/embeddings, and marks derived document provenance —
   * retaining confirmed business records — writing one DELETE_EMAIL_DATA audit.
   */
  deleteEmailData: (payload: EmailDataDeleteRequest) =>
    api.del<EmailDataDeleteResponse>("/privacy/email-data", { body: payload }),

  /**
   * Delete an uploaded document (Requirement 33.4): removes the binary +
   * chunks + embeddings and writes one DELETE_DOCUMENT audit row. Cross-org
   * ids yield ApiError(404).
   */
  deleteDocument: (documentId: string) =>
    api.del<void>(`/privacy/documents/${documentId}`),

  /**
   * Choose the raw-email retention policy and apply it (Requirement 33.5).
   * Selecting EXTRACTED_ONLY drops retained raw message content.
   */
  setRetentionPolicy: (payload: RetentionPolicyUpdate) =>
    apiRequest<RetentionPolicy>("/privacy/retention-policy", {
      method: "PUT",
      body: payload,
    }),

  /** Last successful sync time, last error, and status per connection (33.6). */
  syncStatus: () => api.get<SyncStatus[]>("/privacy/sync-status"),

  /**
   * What data was used for an AI answer (Requirement 33.7): the exact citations
   * and full evidence set that grounded the answer. Cross-org/missing answer
   * ids yield ApiError(404).
   */
  answerProvenance: (answerId: string) =>
    api.get<AnswerProvenance>(`/privacy/answer-provenance/${answerId}`),
};
