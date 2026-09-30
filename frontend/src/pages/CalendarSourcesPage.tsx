import { Link, useParams } from "react-router-dom";
import { workspaceApi, type CalendarSource } from "@/api/workspace";
import { useCachedQuery } from "@/hooks/useCachedQuery";

export default function CalendarSourcesPage() {
  const { sourceId } = useParams();
  const list = useCachedQuery<CalendarSource | CalendarSource[]>(`/workspace/calendar${sourceId ? `/${sourceId}` : ""}`, () => sourceId ? workspaceApi.calendarSource(sourceId) : workspaceApi.calendar());
  const items = list.data ? Array.isArray(list.data) ? list.data : [list.data] : undefined;
  const { error, retry } = list;
  return <section className="mx-auto max-w-4xl px-6 py-8"><h1 className="text-2xl font-semibold">Calendar sources</h1><p className="mt-2 text-sm text-slate-500">Events captured from your primary Google Calendar. Recurring series show their original start date.</p>
    {error ? <div role="alert" className="mt-6 text-sm">Could not load these events. <button onClick={retry} className="text-blue-600 underline">Retry</button></div> : !items ? <p className="mt-6 text-sm text-slate-500">Loading events…</p> : !items.length ? <p className="mt-6 rounded-xl border bg-white p-6 text-sm text-slate-500">No calendar events captured yet. Connect Calendar in Settings, then select Sync Now.</p> : <ul className="mt-6 space-y-4">{items.map(item => <li key={item.id} className="rounded-xl border border-slate-200 bg-white p-5"><span className="text-xs text-blue-600">Original Calendar event</span><h2 className="mt-2 font-semibold"><Link to={`/calendar-sources/${item.id}`}>{item.title}</Link></h2><p className="mt-2 text-sm text-slate-600">{item.starts_at}{item.ends_at && ` — ${item.ends_at}`}</p>{item.location && <p className="mt-2 text-sm text-slate-500">{item.location}</p>}<p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-600">{item.description}</p>{/^https:\/\/(calendar|www)\.google\.com\//.test(item.html_link) && <a href={item.html_link} target="_blank" rel="noreferrer" className="mt-4 inline-block text-sm font-medium text-blue-600">Open in Google Calendar ↗</a>}</li>)}</ul>}
  </section>;
}
