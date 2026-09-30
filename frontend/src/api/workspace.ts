import { api } from "./client";

export type SearchKind = "all" | "email" | "file" | "note" | "knowledge" | "action" | "draft" | "calendar";
export interface WorkspaceResult {
  id: string; kind: SearchKind; title: string; excerpt: string;
  status: string; updated_at: string; path: string;
}
export interface WorkspaceSearch { items: WorkspaceResult[]; total: number; has_more: boolean }
export interface WorkspaceSummary {
  documents: number; documents_pending: number; documents_failed: number;
  notes: number; open_actions: number; knowledge: number;
  source_reviews: number; knowledge_reviews: number;
}
export interface WorkspaceActivity { id: string; label: string; title: string; path: string | null; created_at: string }
export interface ActivityPage { items: WorkspaceActivity[]; has_more: boolean }
export interface CalendarSource {
  id: string; title: string; description: string; location: string;
  starts_at: string; ends_at: string; html_link: string; updated_at: string;
}
export const workspaceApi = {
  search: (q: string, kind: SearchKind = "all", offset = 0, signal?: AbortSignal) =>
    api.get<WorkspaceSearch>(`/workspace/search?${new URLSearchParams({ q, kind, offset: String(offset) })}`, { signal }),
  summary: () => api.get<WorkspaceSummary>("/workspace/summary"),
  syncCalendar: (id: string) => api.post<{ events_synced: number }>(`/calendar/${id}/sync-now`),
  calendar: () => api.get<CalendarSource[]>("/workspace/calendar"),
  calendarSource: (id: string) => api.get<CalendarSource>(`/workspace/calendar/${id}`),
  activity: (offset = 0, limit = 5) => api.get<ActivityPage>(`/workspace/activity?offset=${offset}&limit=${limit}`),
};
