import { Link, useSearchParams } from "react-router-dom";
import { workspaceApi, type SearchKind } from "@/api/workspace";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import WorkspaceIcon, { type IconName } from "@/components/WorkspaceIcon";

export const SEARCH_KINDS: [SearchKind, string][] = [["all", "Everything"], ["email", "Emails"], ["file", "Files"], ["note", "Notes"], ["calendar", "Calendar"], ["knowledge", "Knowledge"], ["action", "Actions"], ["draft", "Drafts"]];
export const RESULT_ICONS: Record<SearchKind, IconName> = { all: "search", email: "email", file: "file", note: "note", calendar: "calendar", knowledge: "note", action: "arrow", draft: "email" };
export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const kind = (SEARCH_KINDS.find(([key]) => key === params.get("kind"))?.[0] ?? "all");
  const offset = Math.min(100000, Math.max(0, Number(params.get("offset")) || 0));
  const path = `/workspace/search?${new URLSearchParams({ q: query, kind, offset: String(offset) })}`;
  const { data, error, retry } = useCachedQuery(path, () => workspaceApi.search(query, kind, offset));
  return <section className="mx-auto max-w-5xl px-4 py-7 sm:px-6">
    <h1 className="text-2xl font-semibold tracking-tight">{query ? `Results for “${query}”` : "Explore your workspace"}</h1>
    <p className="mt-2 text-sm text-slate-500">Find captured source content, saved knowledge and follow-up work in one place.</p>
    <nav aria-label="Search categories" className="my-6 flex flex-wrap gap-2">{SEARCH_KINDS.map(([key, label]) => <button key={key} aria-pressed={key === kind} onClick={() => setParams({ q: query, kind: key })} className={`rounded-full border px-4 py-2 text-xs font-medium ${key === kind ? "border-blue-600 bg-blue-600 text-white" : "border-slate-200 bg-white text-slate-600 hover:border-blue-300"}`}>{label}</button>)}</nav>
    <div aria-live="polite" className="mb-3 text-xs text-slate-500">{error ? "Search could not be loaded." : data ? `${data.total} results${data.total ? ` · ${offset + 1}–${offset + data.items.length}` : ""}` : "Searching…"}</div>
    {error && <button onClick={retry} className="rounded-lg border bg-white px-4 py-2 text-sm">Retry search</button>}
    {data?.items.length === 0 && <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-14 text-center"><WorkspaceIcon name="search" className="mx-auto mb-3 h-7 w-7 text-slate-300" /><h2 className="font-medium">No matching information</h2><p className="mt-2 text-sm text-slate-500">Try another keyword or category, or sync and add your sources.</p></div>}
    <ul className="space-y-3">{data?.items.map(item => <li key={`${item.kind}-${item.id}`}><Link to={item.path} className="group flex gap-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-blue-300 hover:shadow-md">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600"><WorkspaceIcon name={RESULT_ICONS[item.kind]} /></div>
      <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2 text-[10px] font-medium uppercase tracking-wide text-slate-400"><span className="text-blue-600">{item.kind}</span><span>·</span><span>{item.status.replace(/_/g, " ")}</span><span className="ml-auto normal-case tracking-normal">{new Date(item.updated_at).toLocaleDateString()}</span></div><h2 className="mt-1 break-words text-sm font-semibold text-slate-900 group-hover:text-blue-700">{item.title}</h2>{item.excerpt && <p className="mt-2 line-clamp-2 break-words text-sm leading-relaxed text-slate-500">{item.excerpt}</p>}</div><WorkspaceIcon name="arrow" className="mt-3 hidden h-4 w-4 shrink-0 text-slate-300 sm:block" />
    </Link></li>)}</ul>
    {data && (offset > 0 || data.has_more) && <div className="mt-6 flex justify-between"><button disabled={offset === 0} onClick={() => setParams({ q: query, kind, offset: String(Math.max(0, offset - 20)) })} className="rounded-lg border bg-white px-4 py-2 text-sm disabled:opacity-40">Previous</button><button disabled={!data.has_more} onClick={() => setParams({ q: query, kind, offset: String(offset + 20) })} className="rounded-lg border bg-white px-4 py-2 text-sm disabled:opacity-40">Next</button></div>}
  </section>;
}
