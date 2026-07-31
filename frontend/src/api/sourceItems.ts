// Typed API wrappers for the Source Inbox pipeline (Requirements 3, 4, 5, 6).
//
// Every call is cookie-authenticated by the shared `api` client
// (credentials: "include"), so no token handling is needed here. Filters are
// applied server-side; empty filters return the full organization-scoped inbox.
//
// Notable status codes surfaced as ApiError by the client:
//   - 422 on invalid create payloads (Requirement 3.5)
//   - 404 on unknown / cross-tenant ids (Requirement 2.3)
//   - 409 on extract when the privacy/sensitivity gate refuses; in particular
//     HIGHLY_SENSITIVE content requires `acknowledged: true` (Requirements 6.3,
//     6.4, 6.5). Callers should inspect ApiError.status === 409.

import { api } from "./client";
import type {
  BusinessCategory,
  ClassificationOverride,
  ClassificationResult,
  KnowledgeItem,
  SourceItem,
  SourceItemCreate,
  SourceItemDetail,
  SourceStatus,
} from "./types";

/** Optional server-side filters for the inbox list (Requirement 3.2). */
export interface InboxFilters {
  status?: SourceStatus;
  category?: BusinessCategory;
}

function buildInboxQuery(filters: InboxFilters = {}): string {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.category) params.set("category", filters.category);
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const sourceItemsApi = {
  /** List the organization's inbox, filterable by status/category (Req 3.2). */
  list: (filters: InboxFilters = {}) =>
    api.get<SourceItem[]>(`/source-items${buildInboxQuery(filters)}`),

  /** Collect a new source item; persisted in status NEW (Req 3.1). */
  create: (payload: SourceItemCreate) =>
    api.post<SourceItem>("/source-items", payload),

  /** Fetch a single item plus its current classification, if any (Req 3.3). */
  get: (itemId: string) =>
    api.get<SourceItemDetail>(`/source-items/${itemId}`),

  /** Mark a source item DISMISSED (Req 3.4). */
  dismiss: (itemId: string) =>
    api.post<SourceItem>(`/source-items/${itemId}/dismiss`),

  /**
   * Permanently delete a source item (HARD delete, distinct from dismiss).
   * Also deletes its classification results and detaches derived knowledge.
   * Throws ApiError(404) for an unknown or cross-tenant id.
   */
  remove: (itemId: string) => api.del<void>(`/source-items/${itemId}`),

  /** Run AI classification, producing a SUGGESTED result (Req 4.1). */
  classify: (itemId: string) =>
    api.post<ClassificationResult>(`/source-items/${itemId}/classify`),

  /**
   * Confirm the suggested classification (Req 5.1, 5.2). Pass an override to
   * replace any of the three AI-suggested axes; omit it to confirm verbatim.
   */
  confirmClassification: (itemId: string, override?: ClassificationOverride) =>
    api.post<ClassificationResult>(
      `/source-items/${itemId}/confirm-classification`,
      override,
    ),

  /** Reject the suggested classification, retaining it as a signal (Req 5.3). */
  rejectClassification: (itemId: string) =>
    api.post<ClassificationResult>(
      `/source-items/${itemId}/reject-classification`,
    ),

  /**
   * Extract a SUGGESTED knowledge item (Req 6). Throws ApiError(409) when the
   * privacy/sensitivity gate refuses — HIGHLY_SENSITIVE content requires
   * `acknowledged: true` (Req 6.4, 6.5).
   */
  extract: (itemId: string, acknowledged?: boolean) =>
    api.post<KnowledgeItem>(
      `/source-items/${itemId}/extract`,
      acknowledged === undefined ? undefined : { acknowledged },
    ),
};
