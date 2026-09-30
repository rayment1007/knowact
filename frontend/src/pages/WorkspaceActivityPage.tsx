import { Link, useSearchParams } from "react-router-dom";
import { workspaceApi } from "@/api/workspace";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import { formatTimestamp } from "@/utils/workspaceDates";

export default function WorkspaceActivityPage() {
  const [params, setParams] = useSearchParams();
  const offset = Math.min(100000, Math.max(0, Math.floor(Number(params.get("offset")) || 0)));
  const { data, error, retry } = useCachedQuery(`/workspace/activity?offset=${offset}&limit=20`, () => workspaceApi.activity(offset, 20));
  return <section className="mx-auto max-w-4xl px-6 py-8">
    <Link to="/" className="text-xs font-medium text-blue-600">← Dashboard</Link>
    <h1 className="mt-4 text-2xl font-semibold">Recent activity</h1>
    <p className="mt-2 text-sm text-slate-500">Your saved actions, source reviews, knowledge confirmations and document activity.</p>
    {error && <p role="alert" className="mt-5 text-sm text-amber-800">Could not update activity. <button onClick={retry} className="underline">Retry</button></p>}
    {!data && !error && <p className="mt-6 text-sm text-slate-400">Loading activity…</p>}
    {data && !data.items.length && <p className="mt-6 rounded-xl border bg-white p-6 text-sm text-slate-500">No saved activity on this page yet.</p>}
    <ul className="mt-6 divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">{data?.items.map(item => <li key={item.id} className="flex flex-wrap items-start justify-between gap-2 px-5 py-4">
      <div className="min-w-0"><p className="text-xs font-medium text-blue-600">{item.label}</p>{item.path ? <Link to={item.path} className="mt-1 block break-words text-sm font-medium text-slate-800 hover:text-blue-600">{item.title}</Link> : <p className="mt-1 text-sm text-slate-400">{item.title}</p>}</div>
      <time className="text-xs text-slate-400">{formatTimestamp(item.created_at)}</time>
    </li>)}</ul>
    {data && (offset > 0 || data.has_more) && <div className="mt-5 flex justify-between"><button disabled={!offset} onClick={() => setParams({ offset: String(Math.max(0, offset - 20)) })} className="rounded-lg border bg-white px-4 py-2 text-sm disabled:opacity-40">Previous</button><button disabled={!data.has_more} onClick={() => setParams({ offset: String(offset + 20) })} className="rounded-lg border bg-white px-4 py-2 text-sm disabled:opacity-40">Next</button></div>}
  </section>;
}
