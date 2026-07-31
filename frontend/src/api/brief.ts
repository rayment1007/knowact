// Typed API wrapper for the Daily Brief (Requirement 10).
//
// The daily brief is the organization-wide "Recommend + Learn" output:
// generated and persisted on request from the tenant's confirmed knowledge,
// open actions, and recent decisions, and returned with a headline plus
// priorities, recommended actions, follow-ups, and risks — each line grounded
// in supporting evidence (Requirements 10.1, 10.3, 10.4).
//
// The call is cookie-authenticated by the shared `api` client and scoped
// server-side to the caller's organization. This module reuses the shared
// `BriefResponse` type from types.ts.

import { api } from "./client";
import type { BriefResponse } from "./types";

export const briefApi = {
  /**
   * Generate, persist, and return the organization-wide daily brief for the
   * current user (Requirements 10.1, 10.3). The `content` is a
   * `DailyBriefContent` for the DAILY scope.
   */
  daily: () => api.get<BriefResponse>("/brief/daily"),
};
