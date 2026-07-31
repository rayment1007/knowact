// Typed API wrappers for the grounded Enterprise Copilot (M6.5, Req 30, 31).
//
// Every call is cookie-authenticated by the shared `api` client and scoped
// server-side to the caller's organization. The Copilot answers only from
// confirmed, permitted data and returns citations validated against the bounded
// evidence set (no fabricated citations). DRAFT/ACT results are SUGGESTED
// artifacts that require an explicit `confirm` before any mutation.

import { api } from "./client";
import type {
  CopilotAskRequest,
  CopilotConfirmRequest,
  CopilotConfirmResponse,
  CopilotResponse,
  SuggestedQuestionsResponse,
} from "./types";

export const copilotApi = {
  /**
   * Ask a grounded question. Returns an answer + citations, or a SUGGESTED
   * artifact (DRAFT/ACT). No mutation occurs here (Requirement 31.3).
   */
  ask: (payload: CopilotAskRequest) =>
    api.post<CopilotResponse>("/copilot/ask", payload),

  /** The fixed + dynamic, org-scoped suggested questions (Requirement 30.6). */
  suggestedQuestions: () =>
    api.get<SuggestedQuestionsResponse>("/copilot/suggested-questions"),

  /**
   * Confirm a previously-SUGGESTED artifact (confirm-before-mutate). Applying
   * an ACTION_ITEM creates a real action and writes one audit row (Req 31.4).
   */
  confirm: (payload: CopilotConfirmRequest) =>
    api.post<CopilotConfirmResponse>("/copilot/confirm", payload),
};
