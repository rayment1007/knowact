import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { actionsApi } from "@/api";
import { workspaceApi } from "@/api/workspace";
import { useAuth } from "@/auth";
import { useWorkspace } from "@/components/WorkspaceProvider";
import WorkspaceIcon, { type IconName } from "@/components/WorkspaceIcon";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import { dueLabel, eventDate, formatDay, formatTimestamp, localDay, orderedActions, upcomingEvents } from "@/utils/workspaceDates";
import { RESULT_ICONS } from "./SearchPage";

function Panel({ title, icon, link, children, description }: { title: string; icon: IconName; link: { to: string; label: string }; children: ReactNode; description?: string }) {
  return <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
    <header className="px-5 pb-3 pt-5">
      <div className="flex items-center justify-between gap-3"><h2 className="flex items-center gap-2 text-sm font-semibold"><WorkspaceIcon name={icon} className="h-4 w-4 text-blue-600" />{title}</h2><Link to={link.to} className="shrink-0 text-xs font-medium text-blue-600 hover:underline">{link.label} →</Link></div>
      {description && <p className="mt-2 text-[11px] leading-relaxed text-slate-400">{description}</p>}
    </header>
    {children}
  </section>;
}

function QueryNotice({ error, retry, loading, empty, children }: { error: boolean; retry: () => void; loading: boolean; empty: boolean; children: ReactNode }) {
  if (error) return <p role="alert" className="px-5 py-4 text-xs text-amber-800">Could not update this section. <button onClick={retry} className="underline">Retry</button></p>;
  if (loading) return <p className="px-5 py-6 text-xs text-slate-400">Loading…</p>;
  return empty ? <p className="px-5 py-6 text-sm leading-relaxed text-slate-500">{children}</p> : null;
}

export default function EnterpriseDashboardPage() {
  const { user } = useAuth();
  const { summary, connections, loading, syncing, error: statusError, failures } = useWorkspace();
  const recent = useCachedQuery("/workspace/search?q=&kind=all&offset=0", () => workspaceApi.search(""));
  const actions = useCachedQuery("/actions", () => actionsApi.list());
  const calendar = useCachedQuery("/workspace/calendar", () => workspaceApi.calendar());
  const activity = useCachedQuery("/workspace/activity?offset=0&limit=5", () => workspaceApi.activity());
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    // Only the local clock changes: there is no network polling.
    const timer = window.setInterval(() => setNow(new Date()), 30000);
    const visible = () => { if (!document.hidden) setNow(new Date()); };
    document.addEventListener("visibilitychange", visible);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, []);
  const openActions = orderedActions(actions.data ?? []);
  const events = upcomingEvents(calendar.data ?? [], now);
  const today = localDay(now);
  const overdue = openActions.filter(item => item.due_date && item.due_date < today).length;
  const dueToday = openActions.filter(item => item.due_date === today).length;
  const reviews = (summary?.source_reviews ?? 0) + (summary?.knowledge_reviews ?? 0);
  const briefReady = !!summary && !!actions.data && !!calendar.data;
  const briefError = (!summary && !!statusError) || (!actions.data && actions.error) || (!calendar.data && calendar.error);
  const briefStale = !!statusError || actions.error || calendar.error;
  const syncFailed = briefStale || Object.keys(failures).length > 0 || connections.some(c => c.last_error || ["ERROR", "EXPIRED"].includes(c.status));
  const quiet = !openActions.length && !events.length && !reviews;
  const name = user?.full_name?.split(" ")[0] || "there";
  const unit = (count: number, singular: string) => `${count} ${singular}${count === 1 ? "" : "s"}`;
  const headline = overdue ? `${unit(overdue, "action")} overdue. Start there.` : dueToday ? `${unit(dueToday, "action")} due today.` : reviews ? `${unit(reviews, "suggestion")} ready for your review.` : openActions.length ? "A little progress goes a long way." : events.length ? "Your next event is on the horizon." : "Your to-do list is taking a well-earned coffee break.";

  return <div className="mx-auto max-w-7xl px-4 py-7 sm:px-6">
    <header className="mb-6 flex flex-wrap items-center justify-between gap-4">
      <div><p className="mb-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-blue-600">Your workspace</p><h1 className="text-2xl font-semibold tracking-tight">Welcome back, {name}</h1><p className="mt-2 text-sm text-slate-500">A clear view of what needs your attention.</p></div>
      <div className="text-left sm:text-right"><p className="text-xs font-semibold text-slate-800">Today</p><time dateTime={today} className="mt-1 block text-sm text-slate-500">{now.toLocaleDateString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}</time></div>
    </header>
    {!loading && !connections.some(c => c.status === "CONNECTED") && <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-blue-100 bg-blue-50 p-4"><div><h2 className="text-sm font-semibold text-blue-900">Bring your sources together</h2><p className="mt-1 text-xs text-blue-700">Connect Gmail and Calendar. Connected accounts sync when you sign in.</p></div><Link to="/settings" className="rounded-lg bg-blue-600 px-4 py-2 text-xs font-medium text-white">Connect Google services</Link></div>}

    <section aria-labelledby="brief-heading" className="mb-6 rounded-xl border border-blue-100 bg-white p-5 shadow-sm">
      <h2 id="brief-heading" className="flex items-center gap-2 text-sm font-semibold"><WorkspaceIcon name="assistant" className="h-5 w-5 text-blue-600" />Today's brief</h2>
      {briefError ? <p role="status" className="mt-4 text-sm text-amber-800">Your brief is temporarily unavailable. Retry the affected section or select Sync Now.</p> : !briefReady ? <p className="mt-4 text-sm text-slate-400">Getting your workspace summary ready…</p> : <div className="mt-4 grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <div className="rounded-lg bg-blue-50/70 p-4">
          <p className="text-sm font-semibold leading-relaxed text-slate-800">{quiet && (syncFailed || syncing) ? "Your saved workspace is quiet for now." : headline}</p>
          <p className="mt-2 text-sm leading-relaxed text-slate-500">{quiet ? "No open actions, pending reviews or upcoming captured events." : `${unit(openActions.length, "open action")} · ${unit(reviews, "pending review")} · ${unit(events.length, "upcoming captured event")}.`}</p>
          <p className="mt-3 text-[11px] text-slate-400">{syncing ? "Sync in progress. This summary uses the last loaded data." : briefStale ? "Workspace data could not refresh. This brief uses saved data and may be incomplete." : syncFailed ? "Some sources could not sync. This summary may be incomplete." : "Based on your saved actions, suggestions and synced calendar."}</p>
        </div>
        <div className="rounded-lg bg-slate-50 p-4"><h3 className="text-xs font-semibold text-blue-700">Key focus</h3><ul className="mt-2 space-y-2 text-sm text-slate-600">
          {openActions[0] && <li><Link className="hover:text-blue-700" to={`/actions/${openActions[0].id}`}>→ {openActions[0].title}</Link></li>}
          {!!summary?.source_reviews && <li><Link className="hover:text-blue-700" to="/source-inbox?review=1">→ Review {unit(summary.source_reviews, "source suggestion")}</Link></li>}
          {!!summary?.knowledge_reviews && <li><Link className="hover:text-blue-700" to="/knowledge?status=SUGGESTED">→ Review {unit(summary.knowledge_reviews, "knowledge suggestion")}</Link></li>}
          {events[0] && <li><Link className="hover:text-blue-700" to={`/calendar-sources/${events[0].id}`}>→ {events[0].title}</Link></li>}
          {quiet && <li>{syncFailed ? "Retry Sync Now to check your connected sources." : syncing ? "Checking your connected sources…" : "Room to think. Or finally finish that coffee."}</li>}
        </ul></div>
      </div>}
    </section>

    <div className="mb-6 grid grid-cols-2 gap-3 xl:grid-cols-4">{([
      ["Sources to review", summary?.source_reviews, "/source-inbox?review=1", "file", "AI classification suggestions"],
      ["Knowledge to review", summary?.knowledge_reviews, "/knowledge?status=SUGGESTED", "note", "AI knowledge awaiting confirmation"],
      ["Open actions", summary?.open_actions, "/actions", "arrow", "Open and in progress"],
      ["Confirmed knowledge", summary?.knowledge, "/knowledge?status=CONFIRMED", "note", "Reviewed and saved"],
    ] as const).map(([label, count, path, icon, description]) => <Link key={label} to={path} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-blue-300"><div className="flex items-center justify-between gap-2 text-xs font-medium text-slate-600">{label}<WorkspaceIcon name={icon} className="h-4 w-4 shrink-0 text-blue-500" /></div><p className="mt-3 text-3xl font-semibold tracking-tight text-slate-900">{count ?? "—"}</p><p className="mt-2 text-[11px] leading-relaxed text-slate-400">{description}</p></Link>)}</div>

    <div className="mb-6 grid gap-5 xl:grid-cols-3">
      <Panel title="Upcoming events" icon="calendar" link={{ to: "/calendar-sources", label: "View calendar" }} description="Captured dates from your primary calendar. Recurring series are not expanded.">
        <QueryNotice error={calendar.error} retry={calendar.retry} loading={!calendar.data} empty={!events.length}>No upcoming dates in your captured events.</QueryNotice>
        <ul className="divide-y divide-slate-100">{events.slice(0, 3).map(event => <li key={event.id}><Link to={`/calendar-sources/${event.id}`} className="block px-5 py-4 hover:bg-slate-50"><p className="text-xs font-medium text-blue-600">{eventDate(event)}</p><p className="mt-1 line-clamp-2 text-sm font-medium text-slate-800">{event.title}</p>{event.location && <p className="mt-1 truncate text-xs text-slate-400">{event.location}</p>}</Link></li>)}</ul>
      </Panel>
      <Panel title="Actions to focus on" icon="check" link={{ to: "/actions", label: "View all" }} description="Overdue first, then earliest due date. Undated actions follow.">
        <QueryNotice error={actions.error} retry={actions.retry} loading={!actions.data} empty={!openActions.length}>No open actions. A little breathing room.</QueryNotice>
        <ul className="divide-y divide-slate-100">{openActions.slice(0, 3).map(action => { const label = dueLabel(action.due_date, now); return <li key={action.id}><Link to={`/actions/${action.id}`} className="flex items-start gap-3 px-5 py-4 hover:bg-slate-50"><WorkspaceIcon name="arrow" className="mt-0.5 h-4 w-4 shrink-0 text-slate-300" /><div className="min-w-0 flex-1"><p className="line-clamp-2 text-sm font-medium text-slate-800">{action.title}</p><div className="mt-2 flex flex-wrap items-center gap-2"><span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${label === "Overdue" ? "bg-rose-50 text-rose-700" : label === "Due today" ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-500"}`}>{label}</span>{action.due_date && <span className="text-[11px] text-slate-400">{formatDay(action.due_date)}</span>}</div></div></Link></li>; })}</ul>
      </Panel>
      <Panel title="Recent activity" icon="sync" link={{ to: "/activity", label: "View all" }} description="Your saved actions, reviews and document activity.">
        <QueryNotice error={activity.error} retry={activity.retry} loading={!activity.data} empty={!activity.data?.items.length}>Your next saved change will appear here.</QueryNotice>
        <ul className="divide-y divide-slate-100">{activity.data?.items.slice(0, 3).map(item => <li key={item.id} className="px-5 py-4"><p className="text-xs font-medium text-blue-600">{item.label}</p>{item.path ? <Link to={item.path} className="mt-1 block line-clamp-1 text-sm font-medium text-slate-800 hover:text-blue-600">{item.title}</Link> : <p className="mt-1 text-sm text-slate-400">{item.title}</p>}<p className="mt-1 text-[10px] text-slate-400">{formatTimestamp(item.created_at)}</p></li>)}</ul>
      </Panel>
    </div>

    <Panel title="Recent information" icon="file" link={{ to: "/search", label: "Explore all" }}>
      <QueryNotice error={recent.error} retry={recent.retry} loading={!recent.data} empty={!recent.data?.items.length}>A fresh start. Sync your sources, upload a document or add a note.</QueryNotice>
      <ul className="divide-y divide-slate-100">{recent.data?.items.slice(0, 6).map(item => <li key={`${item.kind}-${item.id}`}><Link to={item.path} className="flex items-start gap-3 px-5 py-4 hover:bg-slate-50"><div className="rounded-lg bg-blue-50 p-2 text-blue-600"><WorkspaceIcon name={RESULT_ICONS[item.kind]} className="h-4 w-4" /></div><div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-slate-800">{item.title}</p><p className="mt-1 line-clamp-1 text-xs text-slate-400">{item.excerpt || item.status}</p></div><span className="mt-1 shrink-0 text-[10px] capitalize text-slate-400">{item.kind}</span></Link></li>)}</ul>
    </Panel>
    <Link to="/email-drafts" className="mt-5 flex items-center gap-3 rounded-xl border border-blue-100 bg-blue-50/50 px-5 py-4"><WorkspaceIcon name="email" className="h-5 w-5 text-blue-600" /><span className="flex-1 text-sm font-medium text-slate-700">Continue your email follow-ups in Gmail drafts</span><WorkspaceIcon name="arrow" className="h-4 w-4 text-blue-500" /></Link>
  </div>;
}
