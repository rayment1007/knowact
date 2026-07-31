// Typed API wrappers for the Knowledge Hub (Requirement 7).
//
// Every call is cookie-authenticated by the shared `api` client
// (credentials: "include"), so no token handling is needed here. Filters are
// applied server-side, in addition to the mandatory organization scoping; an
// empty filter returns the full organization-scoped knowledge base
// (Requirement 7.3).
//
// Notable status codes surfaced as ApiError by the client:
//   - 404 on unknown / cross-tenant ids (Requirement 2.3)
//
// The endpoint that *creates* a knowledge item lives on the source-item
// resource (`POST /api/source-items/{id}/extract`, see sourceItems.ts); this
// module covers only the review side — list, detail, confirm, reject.

import { api } from "./client";
import type {
  KnowledgeDetail,
  KnowledgeItem,
  SuggestionStatus,
} from "./types";

/** Optional server-side filters for the knowledge list (Requirement 7.3). */
export interface KnowledgeFilters {
  businessEntityId?: string;
  status?: SuggestionStatus;
}

function buildKnowledgeQuery(filters: KnowledgeFilters = {}): string {
  const params = new URLSearchParams();
  if (filters.businessEntityId)
    params.set("business_entity_id", filters.businessEntityId);
  if (filters.status) params.set("status", filters.status);
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const knowledgeApi = {
  /**
   * List the organization's knowledge items, newest first, optionally filtered
   * by connected business entity and/or suggestion status (Requirement 7.3).
   */
  list: (filters: KnowledgeFilters = {}) =>
    api.get<KnowledgeItem[]>(`/knowledge${buildKnowledgeQuery(filters)}`),

  /**
   * Fetch a single knowledge item together with its evidence and the actions
   * and decisions linked to it (Requirements 7.4, 7.5). Throws ApiError(404)
   * for an unknown or cross-tenant id.
   */
  getDetail: (knowledgeId: string) =>
    api.get<KnowledgeDetail>(`/knowledge/${knowledgeId}`),

  /** Confirm a suggested knowledge item, setting it to CONFIRMED (Req 7.1). */
  confirm: (knowledgeId: string) =>
    api.post<KnowledgeItem>(`/knowledge/${knowledgeId}/confirm`),

  /**
   * Reject a suggested knowledge item, setting it to REJECTED while retaining
   * it as a negative signal (Requirement 7.2).
   */
  reject: (knowledgeId: string) =>
    api.post<KnowledgeItem>(`/knowledge/${knowledgeId}/reject`),

  /**
   * Permanently delete a knowledge item. Detaches (NULLs) the link on any
   * action referencing it. Throws ApiError(404) for an unknown/cross-tenant id.
   */
  remove: (knowledgeId: string) => api.del<void>(`/knowledge/${knowledgeId}`),
};
