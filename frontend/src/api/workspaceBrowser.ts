import { api } from "./client";
import type { CalendarEventLink, DocumentAsset, EmailTaskSuggestion, SourceItem } from "./types";

export type WorkspaceSection = "sources" | "knowledge" | "actions";
export type SourceKind = "source" | "email" | "file" | "calendar";
export type ItemKind = SourceKind | "knowledge" | "action" | "suggestion" | "proposal";
export interface SourceOrigin { kind: SourceKind; id: string; title: string; available: boolean; path: string | null }
export interface ProposalPayload { summary?: string; key_points?: string[]; title?: string; description?: string; due_date?: string | null }
export interface SourceProposal {
  id: string; source_kind: SourceKind; source_id: string; source_title: string;
  target: "knowledge" | "action"; status: "SUGGESTED" | "APPROVED" | "REJECTED";
  payload: ProposalPayload; evidence_text: string; analysis_truncated: boolean;
  provider: "mock" | "openai"; version: number; result_id: string | null;
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
}
export interface SourceDetail {
  id: string; kind: SourceKind; title: string; source: SourceItem | null;
  document: DocumentAsset | null; raw_content: string | null;
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
  generate: (kind: SourceKind, id: string, target: "knowledge" | "action", acknowledge_sensitive = false) => api.post<SourceProposal>(`/workspace/source/${kind}/${id}/proposals`, { target, acknowledge_sensitive }),
  proposal: (id: string) => api.get<SourceProposal>(`/workspace/proposals/${id}`),
  saveProposal: (id: string, version: number, payload: ProposalPayload) => api.patch<SourceProposal>(`/workspace/proposals/${id}`, { version, payload }),
  approveProposal: (id: string, version: number, payload: ProposalPayload) => api.post<SourceProposal>(`/workspace/proposals/${id}/approve`, { version, payload }),
  rejectProposal: (id: string, version: number) => api.post<SourceProposal>(`/workspace/proposals/${id}/reject`, { version }),
};
export const itemLink = (item: { kind: ItemKind; id: string; source_type?: string }) =>
  `/workspace/${item.kind === "knowledge" || (item.kind === "proposal" && item.source_type === "knowledge") ? "knowledge" : ["action", "suggestion", "proposal"].includes(item.kind) ? "actions" : "sources"}?item=${item.kind}:${item.id}`;
