// Typed API wrappers for Decision Memory (Requirement 9).
//
// A decision record is an immutable record of a decision already made, kept
// together with its rationale and the supporting evidence text so the "why"
// behind past choices is never lost (Requirement 9.1/9.2). Every call is
// cookie-authenticated by the shared `api` client and scoped server-side to
// the caller's organization; the list can be filtered by business entity in
// addition to that mandatory scoping (Requirement 9.3).
//
// This module reuses the shared `DecisionRecord` type from types.ts and only
// adds the request payload shape.

import { api } from "./client";
import type { DecisionRecord } from "./types";

/** Optional server-side filters for the decision list (Requirement 9.3). */
export interface DecisionFilters {
  businessEntityId?: string;
}

/** Request body of `POST /api/decisions` (Requirement 9.1). */
export interface DecisionCreate {
  title: string;
  decision: string;
  rationale: string;
  evidence_text: string;
  business_entity_id?: string;
  decided_at?: string;
}

function buildDecisionQuery(filters: DecisionFilters = {}): string {
  const params = new URLSearchParams();
  if (filters.businessEntityId)
    params.set("business_entity_id", filters.businessEntityId);
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const decisionsApi = {
  /**
   * List the organization's decision records, newest first, optionally
   * filtered by connected business entity (Requirement 9.3).
   */
  list: (filters: DecisionFilters = {}) =>
    api.get<DecisionRecord[]>(`/decisions${buildDecisionQuery(filters)}`),

  /** Fetch one organization-scoped decision for a direct detail route. */
  get: (decisionId: string) =>
    api.get<DecisionRecord>(`/decisions/${decisionId}`),

  /**
   * Record a decision together with its rationale and supporting evidence
   * (Requirement 9.1). Returns the newly created immutable record.
   */
  create: (payload: DecisionCreate) =>
    api.post<DecisionRecord>("/decisions", payload),

  /**
   * Permanently delete a decision record. Throws ApiError(404) for an unknown
   * or cross-tenant id.
   */
  remove: (decisionId: string) => api.del<void>(`/decisions/${decisionId}`),
};
