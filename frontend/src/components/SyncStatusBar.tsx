import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useWorkspace } from "./WorkspaceProvider";
import { formatTimestamp } from "@/utils/workspaceDates";
import WorkspaceIcon from "./WorkspaceIcon";

export default function SyncStatusBar() {
  const { connections, syncing, loading, error, failures, sync } = useWorkspace();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const failed = !!error || Object.keys(failures).length > 0 || connections.some(c => c.last_error || ["ERROR", "EXPIRED"].includes(c.status));
  const connected = connections.some(c => c.status === "CONNECTED");
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); } };
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  return <div className="relative shrink-0" ref={root}>
    <button ref={trigger} type="button" aria-label="Sync status" aria-expanded={open} aria-controls="sync-status-details" onClick={() => setOpen(value => !value)} className="flex items-center gap-2 rounded-lg border border-slate-200 px-2.5 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-blue-500">
      <WorkspaceIcon name="sync" className={`h-4 w-4 ${syncing ? "animate-spin text-blue-600" : failed ? "text-amber-600" : connected ? "text-emerald-600" : "text-slate-400"}`} />
      <span className="hidden sm:inline">{syncing ? "Syncing…" : loading ? "Checking…" : failed ? "Sync needs attention" : "Sync status"}</span><span aria-hidden className="text-[10px] text-slate-400">▾</span>
    </button>
    {open && <section id="sync-status-details" aria-label="Google sync details" className="absolute right-0 z-40 mt-2 w-[min(22rem,calc(100vw_-_2rem))] rounded-xl border border-slate-200 bg-white p-4 shadow-xl">
      <div className="mb-4 flex items-center justify-between gap-3"><h2 className="text-sm font-semibold text-slate-900">Connected sources</h2><button onClick={() => void sync()} disabled={syncing} className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50">{syncing ? "Syncing…" : "Sync Now"}</button></div>
      <div className="space-y-4">{([['GMAIL', 'Gmail', 'email'], ['GOOGLE_CALENDAR', 'Calendar', 'calendar']] as const).flatMap(([service, label, icon]) => {
        const found = connections.filter(c => c.service === service);
        return (found.length ? found : [null]).map(connection => {
          const syncFailed = !!connection && !!(failures[connection.id] || connection.last_error);
          const active = connection?.status === "CONNECTED";
          const lastSync = formatTimestamp(connection?.last_sync_at);
          const busy = syncing && active;
          const good = active && !!lastSync && !syncFailed && !busy;
          const status = loading ? "Checking…" : busy ? "Syncing…" : syncFailed ? "⚠ Sync failed" : connection && ["EXPIRED", "ERROR"].includes(connection.status) ? "Reconnect needed" : !active ? "Connect account" : lastSync ? `✓ Last sync: ${lastSync}` : "Not synced yet";
          return <Link key={connection?.id ?? service} to="/settings" onClick={() => setOpen(false)} className="flex items-start gap-2 rounded focus-visible:ring-2 focus-visible:ring-blue-500"><WorkspaceIcon name={icon} className="mt-0.5 h-4 w-4 shrink-0 text-blue-600" /><span className="min-w-0"><span className="block text-xs font-semibold text-slate-800">{label}</span>{found.length > 1 && <span className="block break-all text-[11px] text-slate-500">{connection?.account_email}</span>}<span className={`mt-1 block text-xs ${good ? "text-emerald-700" : syncFailed ? "text-amber-700" : "text-slate-500"}`}>{status}</span>{!good && lastSync && <span className="mt-1 block text-[11px] text-slate-500">Last successful sync: {lastSync}</span>}{syncFailed && !lastSync && <span className="mt-1 block text-[11px] text-slate-500">No successful sync yet</span>}</span></Link>;
        });
      })}</div>
      <p className="mt-4 border-t border-slate-100 pt-3 text-[10px] text-slate-400">Times shown in {Intl.DateTimeFormat().resolvedOptions().timeZone}.</p>
      {(error || Object.keys(failures).length > 0) && <p role="status" className="mt-2 text-xs text-amber-800">{error || [...new Set(Object.values(failures))].join(" ")}</p>}
    </section>}
  </div>;
}
