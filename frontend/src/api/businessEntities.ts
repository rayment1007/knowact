// Typed API wrappers for business entities (Requirement 10.5).
//
// A business entity is a domain object (client, project, vendor, …) that
// accumulates knowledge, actions, and decisions. The Knowledge Hub uses this
// list to populate its "filter by entity" dropdown (Requirement 7.3); the
// Action Center and Decision Memory pages reuse the same list for their own
// entity filters. Every call is cookie-authenticated by the shared `api`
// client and scoped server-side to the caller's organization.

import { api } from "./client";
import type { BusinessEntity } from "./types";

export const businessEntitiesApi = {
  /** List the organization's business entities, newest first. */
  list: () => api.get<BusinessEntity[]>("/business-entities"),
  remove: (id: string) => api.del<void>(`/business-entities/${id}`),
};
