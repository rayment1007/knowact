import { useEffect, useState } from "react";
import { apiRequest } from "@/api/client";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import { inputClass, primaryClass } from "./WorkspacePrimitives";

interface Preferences { email_days: number; calendar_past_days: number; calendar_future_days: number }
export default function SyncPreferences() {
  const query = useCachedQuery("/workspace/sync-preferences", () => apiRequest<Preferences>("/workspace/sync-preferences"));
  const [form, setForm] = useState<Preferences>({ email_days: 7, calendar_past_days: 7, calendar_future_days: 90 });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { if (query.data) setForm(query.data); }, [query.data]);
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setMessage("");
    try { await apiRequest("/workspace/sync-preferences", { method: "PUT", body: form }); setMessage("Saved. Your next sync will use this range."); }
    catch { setMessage("Could not save your sync settings. Please retry."); }
    finally { setBusy(false); }
  }
  return <section className="rounded-xl border border-slate-200 bg-white p-5">
    <h2 className="text-lg font-semibold">Sync range</h2>
    <p className="mt-1 text-sm text-slate-500">Login sync and Sync Now use these settings. Existing workspace items stay when you shorten the range. Removed emails and calendar events can return on your next sync if they are still within this range.</p>
    {query.error ? <p role="alert" className="mt-3 text-sm text-red-600">Sync settings could not be loaded.</p> : <form onSubmit={save} className="mt-4">
      <div className="grid gap-4 sm:grid-cols-3">{([
        ["email_days", "Email: past days", 1], ["calendar_past_days", "Calendar: past days", 0], ["calendar_future_days", "Calendar: next days", 1],
      ] as const).map(([key, label, min]) => <label key={key} className="text-sm text-slate-700">{label}<input className={`${inputClass} mt-2`} type="number" required min={min} max={365} value={form[key]} onChange={event => setForm({ ...form, [key]: Number(event.target.value) })} /></label>)}</div>
      <button className={`${primaryClass} mt-4`} disabled={busy || !query.data}>Save sync range</button>
    </form>}
    {message && <p role="status" className="mt-3 text-sm text-slate-600">{message}</p>}
  </section>;
}
