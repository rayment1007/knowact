import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { businessEntitiesApi } from "@/api";
import { FACT_PATH, factMemoryApi, type FactRelationship, type FactView } from "@/api/factMemory";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import { ErrorState, LoadingState } from "@/components/feedback";
import { formatTimestamp } from "@/utils/workspaceDates";
import WorkspaceIcon from "@/components/WorkspaceIcon";
import FactReview, { FactRelationshipBadge, FactVersionPanel } from "./FactReview";
import { buttonClass, inputClass, Pagination } from "./WorkspacePrimitives";

const SIZE = 20;
const views: [FactView, string][] = [["needs_review", "Needs review"], ["current", "Current"], ["history", "History"]];
const relationships: [FactRelationship, string][] = [["NEW", "New"], ["SAME", "Same"], ["REVISION", "Revision"], ["CONFLICT", "Conflict"], ["UNCLEAR", "Unclear"]];

export default function FactWorkspace() {
  const [params, setParams] = useSearchParams();
  const view = (views.some(([key]) => key === params.get("memory")) ? params.get("memory") : "needs_review") as FactView;
  const relationship = relationships.some(([key]) => key === params.get("relationship")) ? params.get("relationship")! : "";
  const project = params.get("fact_project") ?? "";
  const q = params.get("fact_q") ?? "";
  const pageParam = Number(params.get("fact_page") ?? 1);
  const page = Number.isInteger(pageParam) && pageParam > 0 && pageParam <= 5001 ? pageParam : 1;
  const [search, setSearch] = useState(q);
  const item = params.get("fact") ?? "";
  const [kind, id] = item.split(":");
  const selected = ["proposal", "version"].includes(kind) && /^[a-f0-9-]{36}$/i.test(id ?? "");
  const path = `${FACT_PATH}/memory?${new URLSearchParams({ view, q, limit: String(SIZE), offset: String((page - 1) * SIZE), ...(relationship ? { relationship } : {}), ...(project ? { project_id: project } : {}) })}`;
  const query = useCachedQuery(path, () => factMemoryApi.memory(path));
  const counts = useCachedQuery(`${FACT_PATH}/counts`, factMemoryApi.counts);
  const projects = useCachedQuery("/business-entities", businessEntitiesApi.list);
  const panel = useRef<HTMLElement>(null);
  const lastSelected = useRef<HTMLButtonElement | null>(null);
  function change(values: Record<string, string | null>, filters = true) {
    setParams(previous => {
      const next = new URLSearchParams(previous);
      if (filters) { next.delete("fact"); next.delete("fact_page"); }
      for (const [key, value] of Object.entries(values)) { if (value) next.set(key, value); else next.delete(key); }
      return next;
    });
  }
  useEffect(() => { setSearch(q); }, [q]);
  useEffect(() => { if (selected) panel.current?.focus({ preventScroll: true }); else lastSelected.current?.focus({ preventScroll: true }); }, [selected, item]);
  const filterClass = (active: boolean) => `flex items-center justify-between gap-2 rounded-lg px-3 py-2.5 text-left text-xs xl:w-full ${active ? "bg-blue-50 font-medium text-blue-700" : "text-slate-600 hover:bg-slate-50"}`;
  return <div className="space-y-3">
    <p className="text-xs text-slate-500">Project facts currently cover complete meeting dates. General knowledge is saved separately.</p>
    {!!counts.data?.unresolved && <p role="status" className="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800">{counts.data.unresolved} {counts.data.unresolved === 1 ? "fact needs" : "facts need"} a decision. Conflicting or unclear proposals do not replace accepted knowledge.</p>}
    <div className="grid items-start gap-4 xl:grid-cols-[11rem_minmax(0,1fr)]">
      <aside aria-label="Project fact filters" className="space-y-4 rounded-xl border border-slate-200 bg-white p-3">
        <div><h2 className="mb-2 px-2 text-xs font-semibold text-slate-500">Fact memory</h2><div className="flex flex-wrap gap-1 xl:block">{views.map(([key, label]) => <button type="button" key={key} aria-pressed={view === key} className={filterClass(view === key)} onClick={() => change({ memory: key, relationship: null })}>{label}<span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">{counts.data?.[key] ?? "?"}</span></button>)}</div></div>
        {view === "needs_review" && <div className="border-t border-slate-100 pt-3"><h2 className="mb-2 px-2 text-xs font-semibold text-slate-500">Relationship</h2><div className="flex flex-wrap gap-1 xl:block"><button type="button" className={filterClass(!relationship)} aria-pressed={!relationship} onClick={() => change({ relationship: null })}>All relationships</button>{relationships.map(([key, label]) => <button type="button" key={key} className={filterClass(relationship === key)} aria-pressed={relationship === key} onClick={() => change({ relationship: key })}>{label}</button>)}</div></div>}
        <div className="border-t border-slate-100 pt-3"><h2 className="mb-2 px-2 text-xs font-semibold text-slate-500">Project</h2><div className="flex flex-wrap gap-1 xl:block"><button type="button" className={filterClass(!project)} aria-pressed={!project} onClick={() => change({ fact_project: null })}>All projects</button>{projects.data?.filter(row => row.entity_type === "PROJECT").map(row => <button type="button" key={row.id} className={filterClass(project === row.id)} aria-pressed={project === row.id} onClick={() => change({ fact_project: row.id })}>{row.name}</button>)}</div>{projects.error && <ErrorState message="Could not load projects." onRetry={projects.retry} />}</div>
      </aside>
      <div className={`grid min-w-0 items-start gap-4 ${selected ? "lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]" : "grid-cols-1"}`}>
        <div className={`min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white ${selected ? "hidden lg:block" : ""}`}>
          <div className="space-y-3 border-b border-slate-100 p-4"><h2 className="text-sm font-semibold text-slate-900">{view === "needs_review" ? "Facts awaiting review" : view === "current" ? "Current project facts" : "Fact version history"}</h2><form role="search" className="flex gap-2" onSubmit={event => { event.preventDefault(); change({ fact_q: search.trim() || null }); }}><input aria-label="Search project facts" className={inputClass} maxLength={160} value={search} onChange={event => setSearch(event.target.value)} placeholder="Search meeting identities…" /><button type="submit" aria-label="Search facts" className={buttonClass}><WorkspaceIcon name="search" className="h-4 w-4" /></button></form></div>
          {query.error ? <ErrorState className="m-4" message="Could not load project facts." onRetry={query.retry} /> : !query.data ? <LoadingState className="p-4" label="Loading project facts…" /> : <><ul className="max-h-[68vh] divide-y divide-slate-100 overflow-y-auto">{query.data.items.map(row => {
            const key = `${row.record_type}:${row.id}`;
            return <li key={key}><button type="button" className={`w-full space-y-2 border-l-2 p-4 text-left hover:bg-slate-50 ${item === key ? "border-blue-600 bg-blue-50/60" : "border-transparent"}`} aria-current={item === key ? "true" : undefined} onClick={event => { lastSelected.current = event.currentTarget; change({ fact: key }, false); }}><span className="flex flex-wrap items-start justify-between gap-2"><span className="text-sm font-semibold text-slate-800">{row.title}</span>{row.relationship && <FactRelationshipBadge relationship={row.relationship} />}</span><span className="block text-xs text-slate-600">{row.value_date ?? "Needs clarification"} · {row.project_name}</span><span className="block text-[11px] text-slate-500">{row.status === "CURRENT" ? "Current" : row.status === "SUPERSEDED" ? "Superseded" : row.status === "UNRESOLVED" ? "Unresolved" : "Needs review"}{row.version ? ` · Version ${row.version.version_number}` : ""} · {formatTimestamp(row.created_at)}</span></button></li>;
          })}</ul>{!query.data.items.length && <div className="px-4 py-12 text-center text-sm text-slate-500">{q || project || relationship ? "No facts match these filters." : view === "needs_review" ? "No facts are waiting for review. Select a source and prepare project facts to begin." : "No approved fact versions yet."}</div>}<Pagination total={query.data.total} page={page} size={SIZE} onPage={next => change({ fact_page: String(next), fact: null }, false)} /></>}
        </div>
        {selected && <aside ref={panel} tabIndex={-1} aria-label="Project fact details" className="min-w-0 overflow-hidden rounded-xl border border-slate-200 bg-white focus:outline-none"><header className="flex items-center justify-between border-b border-slate-100 px-5 py-3"><h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{kind === "proposal" ? "Fact review" : "Fact version"}</h2><button type="button" aria-label="Close fact details" className={buttonClass} onClick={() => change({ fact: null }, false)}><WorkspaceIcon name="close" className="h-4 w-4" /></button></header><div className="max-h-[80vh] overflow-y-auto p-5">{kind === "proposal" ? <FactReview key={id} id={id} /> : <FactVersionPanel key={id} id={id} />}</div></aside>}
      </div>
    </div>
  </div>;
}
