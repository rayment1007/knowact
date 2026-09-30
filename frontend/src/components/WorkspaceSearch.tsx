import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { workspaceApi, type WorkspaceSearch as SearchResponse } from "@/api/workspace";
import WorkspaceIcon from "./WorkspaceIcon";

export default function WorkspaceSearch() {
  const navigate = useNavigate();
  const location = useLocation();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [results, setResults] = useState<SearchResponse | null>(null);
  const [error, setError] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setOpen(false);
    setQuery(location.pathname === "/search" ? new URLSearchParams(location.search).get("q") ?? "" : "");
  }, [location.pathname, location.search]);
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "k") { event.preventDefault(); input.current?.focus(); }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);
  useEffect(() => {
    setResults(null); setError(false);
    if (!open || !query.trim()) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      void workspaceApi.search(query.trim(), "all", 0, controller.signal).then(value => {
        if (!controller.signal.aborted) setResults(value);
      }).catch(() => { if (!controller.signal.aborted) setError(true); });
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, open]);
  const destination = `/search?${new URLSearchParams({ q: query.trim() })}`;
  return <div className="relative min-w-0 flex-1 md:max-w-md" onBlur={event => {
    if (!event.currentTarget.contains(event.relatedTarget as Node)) setOpen(false);
  }} onKeyDown={event => { if (event.key === "Escape") setOpen(false); }}>
    <form role="search" onSubmit={event => { event.preventDefault(); setOpen(false); navigate(destination); }}>
      <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white/80 px-3 py-2.5 focus-within:border-blue-400 focus-within:ring-2 focus-within:ring-blue-100">
        <WorkspaceIcon name="search" className="h-4 w-4 shrink-0 text-slate-400" />
        <input ref={input} type="search" aria-label="Search workspace" maxLength={200} value={query} onFocus={() => setOpen(true)} onChange={event => { setQuery(event.target.value); setOpen(true); }} placeholder="Search knowledge, actions, or sources…" className="w-full min-w-0 bg-transparent text-xs text-slate-800 outline-none placeholder:text-slate-400" />
        <kbd className="hidden shrink-0 text-[10px] text-slate-400 xl:block">Ctrl K</kbd>
      </div>
    </form>
    {open && query.trim() && <div className="absolute left-0 right-0 top-full z-40 mt-2 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl">
      <div role="status" className="border-b border-slate-100 px-4 py-3 text-xs text-slate-500">{error ? "Search unavailable. Please retry." : !results ? "Searching…" : `${results.total} matching results`}</div>
      <ul>{results?.items.slice(0, 5).map(result => <li key={`${result.kind}-${result.id}`}><Link to={result.path} onClick={() => setOpen(false)} className="block px-4 py-3 hover:bg-blue-50 focus:bg-blue-50"><span className="mb-1 block text-[10px] font-semibold uppercase tracking-wider text-blue-600">{result.kind}</span><span className="block truncate text-sm font-medium text-slate-800">{result.title}</span><span className="mt-1 block truncate text-xs text-slate-500">{result.excerpt || result.status}</span></Link></li>)}</ul>
      <Link to={destination} onClick={() => setOpen(false)} className="block border-t border-slate-100 px-4 py-3 text-xs font-semibold text-blue-600 hover:bg-blue-50">View all results →</Link>
    </div>}
  </div>;
}
