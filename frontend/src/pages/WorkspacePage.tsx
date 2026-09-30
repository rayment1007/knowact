import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { browserApi, type ItemKind, type SourceKind, type WorkspaceSection } from "@/api/workspaceBrowser";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import WorkspaceIcon, { type IconName } from "@/components/WorkspaceIcon";
import { ErrorState, LoadingState } from "@/components/feedback";
import AddSourceDialog from "@/components/workspace/AddSourceDialog";
import ProposalDetail from "@/components/workspace/SourceProposal";
import SourceDetail from "@/components/workspace/SourceDetail";
import ActionDetail, { AddActionDialog, SuggestionDetail } from "@/components/workspace/ActionDetail";
import { KnowledgeDetailPanel } from "@/pages/KnowledgeHubPage";
import { inputClass, Pagination, primaryClass, StateBadge, stateLabel } from "@/components/workspace/WorkspacePrimitives";
import { formatTimestamp } from "@/utils/workspaceDates";
import { businessEntitiesApi, type BusinessEntity } from "@/api";

const PAGE_SIZE = 20;
const sections = [["sources", "Sources"], ["knowledge", "Knowledge"], ["actions", "Actions"]] as const;
const types = [["all", "All sources", "file"], ["email", "Emails", "email"], ["calendar", "Calendar", "calendar"], ["file", "Files", "file"], ["note", "Notes", "note"]] as const;
const statuses: Record<WorkspaceSection, string[]> = {
  sources: ["NEEDS_REVIEW", "RAW", "REVIEWED", "UPLOADED", "PROCESSING", "INDEXED", "FAILED", "ARCHIVED", "DISMISSED"],
  knowledge: ["SUGGESTED", "CONFIRMED", "REJECTED"],
  actions: ["SUGGESTED", "OPEN", "IN_PROGRESS", "DONE", "CANCELLED"],
};
const noChange = () => {};

export default function WorkspacePage() {
  const { section: routeSection } = useParams<{ section: string }>();
  const section: WorkspaceSection = sections.some(([key]) => key === routeSection) ? routeSection as WorkspaceSection : "sources";
  const [params, setParams] = useSearchParams();
  const [searchText, setSearchText] = useState(params.get("q") ?? "");
  const [addingAction, setAddingAction] = useState(false);
  const [entities, setEntities] = useState<BusinessEntity[]>([]);
  const panel = useRef<HTMLElement>(null);
  const lastSelected = useRef<HTMLButtonElement | null>(null);
  const q = params.get("q") ?? "";
  const requestedPage = Number(params.get("page") ?? 1);
  const page = Number.isInteger(requestedPage) && requestedPage >= 1 && requestedPage <= 5001 ? requestedPage : 1;
  const sourceType = types.some(([key]) => key === params.get("type")) ? params.get("type")! : "all";
  const status = statuses[section].includes(params.get("status") ?? "") ? params.get("status")! : "";
  const order = params.get("order") === "oldest" ? "oldest" : "newest";
  const entity = params.get("entity") ?? "";
  const item = params.get("item") ?? "";
  const [selectedKind, selectedId] = item.split(":") as [ItemKind, string];
  const allowedKinds = section === "sources" ? ["source", "email", "file", "calendar"] : section === "knowledge" ? ["knowledge", "proposal"] : ["action", "suggestion", "proposal"];
  const selected = allowedKinds.includes(selectedKind) && /^[0-9a-f-]{36}$/i.test(selectedId ?? "");
  const path = `/workspace/items?${new URLSearchParams({ section, q, source_type: sourceType, status, order, ...(entity ? { entity_id: entity } : {}), offset: String((page - 1) * PAGE_SIZE), limit: String(PAGE_SIZE) })}`;
  const query = useCachedQuery(path, () => browserApi.items(path));
  const counts = useCachedQuery("/workspace/counts", browserApi.counts);
  const setQuery = useCallback((values: Record<string, string | null>, replace = false) => {
    setParams(current => { const next = new URLSearchParams(current); for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); } return next; }, { replace });
  }, [setParams]);
  const closeDetail = useCallback(() => setQuery({ item: null }), [setQuery]);
  const changeFilter = (values: Record<string, string | null>) => setQuery({ ...values, page: null, item: null });
  useEffect(() => { setSearchText(q); }, [q, section]);
  useEffect(() => {
    if (section === "sources") return;
    let active = true;
    void businessEntitiesApi.list().then(result => { if (active) setEntities(result); }).catch(() => {});
    return () => { active = false; };
  }, [section]);
  const entityNames = useMemo(() => new Map(entities.map(value => [value.id, value.name])), [entities]);
  useEffect(() => {
    if (selected) panel.current?.focus({ preventScroll: true });
    else lastSelected.current?.focus({ preventScroll: true });
  }, [selected, item]);
  // Deleting the final record on a page returns to the last available page.
  useEffect(() => {
    if (query.data && page > Math.max(1, Math.ceil(query.data.total / PAGE_SIZE))) {
      setQuery({ page: String(Math.max(1, Math.ceil(query.data.total / PAGE_SIZE))) }, true);
    }
  }, [page, query.data, setQuery]);
  const reviewCount = counts.data?.[`${section}_reviews`];
  return <section className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6">
    <header className="mb-5 flex flex-wrap items-center justify-between gap-4">
      <div><h1 className="text-2xl font-semibold tracking-tight text-slate-900">Workspace</h1><p className="mt-1 text-sm text-slate-500">Original sources, reviewed knowledge, and your next steps.</p></div>
      <div className="flex items-center gap-3">{section === "actions" && <><Link to="/email-drafts" className="text-sm font-medium text-blue-700 hover:underline">Gmail drafts →</Link><button className={primaryClass} onClick={() => setAddingAction(true)}><WorkspaceIcon name="plus" className="h-4 w-4" />Add action</button></>}
        {section === "sources" && <button className={primaryClass} onClick={() => setQuery({ add: "1" })}><WorkspaceIcon name="plus" className="h-4 w-4" />Add source</button>}
      </div>
    </header>
    <nav aria-label="Workspace sections" className="mb-5 flex gap-2 border-b border-slate-200">{sections.map(([key, label]) => {
      const pending = counts.data?.[`${key}_reviews`] ?? 0;
      return <Link key={key} to={`/workspace/${key}`} aria-current={section === key ? "page" : undefined} className={`flex items-center gap-2 border-b-2 px-3 py-3 text-sm font-semibold sm:px-5 ${section === key ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500 hover:text-slate-800"}`}>{label}{pending > 0 && <span title={`${pending} pending ${key === "sources" ? "classification" : key === "actions" ? "action suggestion" : "knowledge"} reviews`} aria-label={`${pending} pending reviews`} className="rounded-full bg-rose-50 px-1.5 py-0.5 text-[10px] font-semibold text-rose-700">{pending}</span>}</Link>;
    })}</nav>
    {counts.error && <p role="status" className="mb-3 text-xs text-amber-700">Review counts could not be refreshed. <button className="underline" onClick={counts.retry}>Retry</button></p>}
    <div className="grid items-start gap-4 xl:grid-cols-[11rem_minmax(0,1fr)]">
      <aside aria-label={`${section === "sources" ? "Source" : section === "knowledge" ? "Knowledge" : "Action"} filters`} className="space-y-4 rounded-xl border border-slate-200 bg-white p-3">
        {section === "sources" && <div className="flex flex-wrap gap-1 xl:block">{types.map(([value, label, icon]) => <button key={value} aria-pressed={sourceType === value} onClick={() => changeFilter({ type: value === "all" ? null : value })} className={`flex items-center gap-2 rounded-lg px-3 py-2.5 text-left text-xs font-medium xl:w-full ${sourceType === value ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50"}`}><WorkspaceIcon name={icon} className="h-4 w-4" /><span>{label}</span><span className="ml-auto rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{query.data ? value === "all" ? Object.values(query.data.type_counts).reduce((sum, count) => sum + count, 0) : query.data.type_counts[value] ?? 0 : "?"}</span></button>)}</div>}
        <div className={section === "sources" ? "border-t border-slate-100 pt-3" : ""}>
          <h2 className="mb-2 px-2 text-xs font-semibold text-slate-500">{section === "sources" ? "Source status" : section === "knowledge" ? "Review status" : "Task status"}</h2>
          <div className="flex flex-wrap gap-1 xl:block" role="group" aria-label="Status filters">
            {["", ...statuses[section]].map(value => <button key={value} aria-pressed={status === value} onClick={() => changeFilter({ status: value || null })} className={`flex items-center gap-2 rounded-lg px-3 py-2.5 text-left text-xs font-medium xl:w-full ${status === value ? "bg-blue-50 text-blue-700" : "text-slate-600 hover:bg-slate-50"}`}>
              <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${["SUGGESTED", "NEEDS_REVIEW", "FAILED"].includes(value) ? "bg-rose-400" : ["DONE", "CONFIRMED", "REVIEWED", "INDEXED"].includes(value) ? "bg-emerald-400" : ["PROCESSING", "IN_PROGRESS"].includes(value) ? "bg-amber-400" : "bg-slate-300"}`} />
              <span>{value ? stateLabel(value) : "All statuses"}</span>
              <span className="ml-auto rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{query.data ? value ? query.data.status_counts[value] ?? 0 : Object.values(query.data.status_counts).reduce((sum, count) => sum + count, 0) : "?"}</span>
            </button>)}
          </div>
        </div>
        {section !== "sources" && (entities.length > 0 || entity) && <div className="border-t border-slate-100 pt-3"><h2 className="mb-2 px-2 text-xs font-semibold text-slate-500">Project / entity</h2><div className="flex flex-wrap gap-1 xl:block">{[{ id: "", name: "All projects / entities" }, ...entities].map(value => <button key={value.id} aria-pressed={entity === value.id} className={`block rounded-lg px-3 py-2.5 text-left text-xs xl:w-full ${entity === value.id ? "bg-blue-50 font-medium text-blue-700" : "text-slate-600 hover:bg-slate-50"}`} onClick={() => changeFilter({ entity: value.id || null })}>{value.name}</button>)}</div></div>}
      </aside>
      <div className={`grid min-w-0 items-start gap-4 ${selected ? "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]" : "grid-cols-1"}`}>
        <div className={`min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white ${selected ? "hidden lg:block" : ""}`}>
          <div className="space-y-3 border-b border-slate-100 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold text-slate-900">{section === "sources" ? "Source items" : section === "knowledge" ? "Knowledge items" : "Actions & suggestions"}</h2>{!!reviewCount && <button className="text-xs font-medium text-rose-700 hover:underline" onClick={() => changeFilter({ status: section === "sources" ? "NEEDS_REVIEW" : "SUGGESTED", type: null })}>Review {reviewCount} pending</button>}</div>
            <form className="flex gap-2" role="search" onSubmit={event => { event.preventDefault(); changeFilter({ q: searchText.trim() || null }); }}>
              <input aria-label={`Search ${section}`} className={inputClass} value={searchText} maxLength={200} onChange={event => setSearchText(event.target.value)} placeholder={`Search ${section}…`} /><button type="submit" aria-label="Search workspace items" className="rounded-lg border border-slate-200 px-3 text-slate-500 hover:bg-slate-50"><WorkspaceIcon name="search" className="h-4 w-4" /></button>
            </form>
            <div className="flex flex-wrap items-center gap-2">
              <select aria-label="Sort order" className={`${inputClass} !w-auto`} value={order} onChange={event => changeFilter({ order: event.target.value })}><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select>
              {(q || status || sourceType !== "all" || entity) && <button className="text-xs font-medium text-blue-700" onClick={() => changeFilter({ q: null, status: null, type: null, entity: null })}>Clear filters</button>}
            </div>
          </div>
          {query.error ? <ErrorState className="m-4" message="Could not load workspace items." onRetry={query.retry} /> : !query.data ? <LoadingState label="Loading workspace…" className="p-5" /> : query.data.items.length === 0 ? <div className="px-5 py-14 text-center"><p className="text-sm font-medium text-slate-700">No items match this view.</p><p className="mt-2 text-xs text-slate-500">{q || status || sourceType !== "all" ? "Try another filter or search." : section === "sources" ? "Sync a connected account or add a note or file." : section === "knowledge" ? "Knowledge proposals appear after extraction from a source." : "Add a task, or review an AI action suggestion from a source."}</p></div> : <ul className="max-h-[68vh] divide-y divide-slate-100 overflow-y-auto">
            {query.data.items.map(row => {
              const key = `${row.kind}:${row.id}`;
              const icon: IconName = row.source_type === "knowledge" ? "note" : row.source_type === "action" ? "check" : ["email", "calendar", "file"].includes(row.source_type) ? row.source_type as IconName : "note";
              return <li key={key}><button aria-label={row.title} aria-current={item === key ? "true" : undefined} onClick={event => { lastSelected.current = event.currentTarget; setQuery({ item: key }); }} className={`flex w-full items-start gap-3 border-l-2 px-4 py-4 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500 ${item === key ? "border-blue-600 bg-blue-50/60" : "border-transparent hover:bg-slate-50"}`}>
                <span className={`rounded-lg p-2 ${row.source_type === "knowledge" ? "bg-violet-50 text-violet-600" : row.source_type === "action" ? "bg-emerald-50 text-emerald-600" : "bg-blue-50 text-blue-600"}`}><WorkspaceIcon name={icon} className="h-4 w-4" /></span>
                <span className="min-w-0 flex-1"><span className="flex flex-wrap items-start justify-between gap-2"><span className="line-clamp-2 text-sm font-semibold text-slate-800">{row.title || "Untitled"}</span><StateBadge status={row.status} /></span><span className="mt-1.5 line-clamp-2 break-words text-xs leading-relaxed text-slate-500">{row.excerpt || (row.kind === "file" ? "Open to process or inspect this file." : "Open to see details.")}</span><span className="mt-2 block text-[10px] text-slate-400">{row.kind === "proposal" ? "Your AI draft" : row.kind === "suggestion" ? "AI suggestion" : row.source_type.charAt(0).toUpperCase() + row.source_type.slice(1)} · {formatTimestamp(row.updated_at)}</span></span>
              </button></li>;
            })}
          </ul>}
          {query.data && !query.error && <Pagination total={query.data.total} page={page} size={PAGE_SIZE} onPage={next => setQuery({ page: String(next), item: null })} />}
        </div>
        {selected && <aside ref={panel} tabIndex={-1} aria-label="Item details" className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white focus:outline-none">
          <header className="flex items-center justify-between border-b border-slate-100 px-5 py-3"><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">{section === "sources" ? "Source details" : section === "knowledge" ? "Knowledge details" : "Action details"}</span><button aria-label="Close details" title="Close details" className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700" onClick={closeDetail}><WorkspaceIcon name="close" className="h-4 w-4" /></button></header>
          <div className="max-h-[80vh] overflow-y-auto">
            {section === "sources" && <SourceDetail key={item} kind={selectedKind as SourceKind} id={selectedId} onDeleted={closeDetail} />}
            {selectedKind === "knowledge" && <KnowledgeDetailPanel key={item} knowledgeId={selectedId} entityNameById={entityNames} onStatusChanged={noChange} onDeleted={closeDetail} />}
            {selectedKind === "proposal" && <ProposalDetail key={item} id={selectedId} onClosed={closeDetail} />}
            {selectedKind === "action" && <ActionDetail key={item} id={selectedId} onDeleted={closeDetail} />}
            {selectedKind === "suggestion" && <SuggestionDetail key={item} id={selectedId} onAccepted={closeDetail} />}
          </div>
        </aside>}
      </div>
    </div>
    {params.get("add") === "1" && <AddSourceDialog onClose={() => setQuery({ add: null }, true)} onCreated={(kind, id) => { setParams({ item: `${kind}:${id}` }); }} />}
    {addingAction && <AddActionDialog onClose={() => setAddingAction(false)} />}
  </section>;
}

/** Bookmarks, dashboard links, and search results keep opening the same record. */
export function LegacyWorkspaceRedirect() {
  const location = useLocation();
  const parts = location.pathname.split("/").filter(Boolean);
  const root = parts[0], id = parts[1];
  const params = new URLSearchParams(location.search);
  const section = root === "knowledge" ? "knowledge" : root === "actions" ? "actions" : "sources";
  const kind = root === "knowledge" ? "knowledge" : root === "actions" ? "action" : root === "documents" ? "file" : root === "calendar-sources" ? "calendar" : root === "emails" || root === "gmail" ? "email" : "source";
  if (id) params.set("item", `${kind}:${id}`);
  if (["documents", "calendar-sources", "gmail", "emails"].includes(root)) params.set("type", kind);
  if (params.get("review") === "1") { params.delete("review"); params.set("status", "NEEDS_REVIEW"); }
  if (params.has("business_entity_id")) { params.set("entity", params.get("business_entity_id")!); params.delete("business_entity_id"); }
  return <Navigate to={`/workspace/${section}${params.size ? `?${params}` : ""}`} replace />;
}
