import { api } from "./client";
import type { CalendarEventLink, DocumentAsset, EmailTaskSuggestion, SourceItem } from "./types";

export type WorkspaceSection = "sources" | "knowledge" | "actions";
export type SourceKind = "source" | "email" | "file" | "calendar";
export type ItemKind = SourceKind | "knowledge" | "action" | "suggestion" | "proposal";
export interface SourceOrigin { kind: SourceKind; id: string; title: string; available: boolean; path: string | null }
export interface ProposalPayload { summary?: string; key_points?: string[]; title?: string; description?: string; due_date?: string | null }
export interface FactCandidate {
  fact_type: "MEETING_TIME"; subject: string; occurrence: string | null;
  value_date: string | null; original_value: string;
  modality: "ASSERTION" | "PROPOSED_CHANGE" | "QUESTION" | "UNCERTAIN";
  previous_value_date: string | null; change_cue: string | null; evidence_text: string | null;
  source_context: "DIRECT" | "QUOTED" | "FORWARDED" | "UNCLEAR";
  outcome: "PROPOSED" | "NEEDS_CLARIFICATION" | "UNSUPPORTED"; reason_codes: string[];
  proposal_id: string | null; proposal_revision: number | null; proposal_status: string | null;
  active_resolution_id?: string | null; resolved_status?: string | null;
}
export interface FactExtraction {
  id: string; status: "SUCCEEDED"; project_id: string; project_name?: string | null; generation: number;
  coverage: { total_characters: number; analyzed_characters: number; ranges: { start: number; end: number }[]; partial: boolean };
  warnings: string[]; candidates: FactCandidate[];
  redacted_at?: string | null;
}
export interface FactGenerateOptions { extract_facts: true; project_id: string; request_key: string; regenerate?: boolean }
export interface FactExtractionCapabilities { enabled: boolean; supported_fact_types: string[]; max_characters: number; max_segments: number }
export interface SourceProposal {
  id: string; source_kind: SourceKind; source_id: string; source_title: string;
  target: "knowledge" | "action"; status: "SUGGESTED" | "APPROVED" | "REJECTED";
  payload: ProposalPayload; evidence_text: string; analysis_truncated: boolean;
  provider: "mock" | "openai"; version: number; result_id: string | null;
  fact_extraction?: FactExtraction | null;
}
export interface WorkspaceItem {
  id: string; kind: ItemKind; source_type: string; title: string;
  excerpt: string; status: string; updated_at: string;
}
export interface ItemPage<T = WorkspaceItem> { items: T[]; total: number; has_more: boolean }
export interface WorkspaceItemPage extends ItemPage {
  type_counts: Record<string, number>; status_counts: Record<string, number>;
}
export interface WorkspaceCounts {
  sources: number; knowledge: number; actions: number;
  sources_reviews: number; knowledge_reviews: number; actions_reviews: number;
  fact_reviews?: number; fact_unresolved?: number; fact_current?: number;
}
export interface SourceDetail {
  id: string; kind: SourceKind; title: string; source: SourceItem | null;
  document: DocumentAsset | null; raw_content: string | null;
  created_from?: { label: string; path: string } | null;
  metadata: { sender?: string; recipients?: string[]; received_at?: string; gmail_message_id?: string;
    starts_at?: string; ends_at?: string; location?: string; html_link?: string };
}
export const browserApi = {
  items: (path: string) => api.get<WorkspaceItemPage>(path),
  counts: () => api.get<WorkspaceCounts>("/workspace/counts"),
  source: (kind: SourceKind, id: string) => api.get<SourceDetail>(`/workspace/source/${kind}/${id}`),
  links: (kind: SourceKind, id: string, offset: number) => api.get<ItemPage>(`/workspace/source/${kind}/${id}/links?offset=${offset}&limit=10`),
  chunks: (id: string, offset: number) => api.get<ItemPage<{ id: string; chunk_index: number; text: string }>>(`/workspace/files/${id}/chunks?offset=${offset}&limit=10`),
  suggestion: (id: string) => api.get<EmailTaskSuggestion>(`/workspace/suggestions/${id}`),
  actionContext: (id: string) => api.get<{ email_id: string | null; origins: SourceOrigin[]; calendar_links: CalendarEventLink[] }>(`/workspace/actions/${id}/context`),
  knowledgeOrigin: (id: string) => api.get<SourceOrigin[]>(`/workspace/knowledge/${id}/origin`),
  factCapabilities: () => api.get<FactExtractionCapabilities>("/workspace/fact-extraction/capabilities"),
  generate: (kind: SourceKind, id: string, target: "knowledge" | "action", acknowledge_sensitive = false, options?: FactGenerateOptions) => api.post<SourceProposal>(`/workspace/source/${kind}/${id}/proposals`, { target, acknowledge_sensitive, ...options }),
  proposal: (id: string) => api.get<SourceProposal>(`/workspace/proposals/${id}`),
  saveProposal: (id: string, version: number, payload: ProposalPayload) => api.patch<SourceProposal>(`/workspace/proposals/${id}`, { version, payload }),
  approveProposal: (id: string, version: number, payload: ProposalPayload) => api.post<SourceProposal>(`/workspace/proposals/${id}/approve`, { version, payload }),
  rejectProposal: (id: string, version: number) => api.post<SourceProposal>(`/workspace/proposals/${id}/reject`, { version }),
  editFact: (id: string, expected_revision: number, value_date: string, excerpt: string, request_key: string) => api.patch(`/dev/fact-memory/proposals/${id}`, { expected_revision, value_date, excerpt, request_key }),
};
export const itemLink = (item: { kind: ItemKind; id: string; source_type?: string }) =>
  `/workspace/${item.kind === "knowledge" || (item.kind === "proposal" && item.source_type === "knowledge") ? "knowledge" : ["action", "suggestion", "proposal"].includes(item.kind) ? "actions" : "sources"}?item=${item.kind}:${item.id}`;
