import { useState } from "react";
import { businessEntitiesApi, type BusinessEntity } from "@/api";

export default function ManageProjects({ entities, onDeleted }: { entities: BusinessEntity[]; onDeleted: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  async function remove(entity: BusinessEntity) {
    if (!window.confirm(`Delete the “${entity.name}” group? Its knowledge and actions will be kept without this group.`)) return;
    setBusy(entity.id); setError("");
    try { await businessEntitiesApi.remove(entity.id); onDeleted(entity.id); }
    catch { setError("Could not delete this group. Please retry."); }
    finally { setBusy(null); }
  }
  return <div className="mt-2 px-2 text-xs">
    <button onClick={() => setOpen(!open)} aria-expanded={open} className="text-blue-600 hover:underline">{open ? "Close group manager" : "Manage groups"}</button>
    {open && <div className="mt-3 space-y-3"><p className="text-slate-500">Deleting a group keeps its knowledge and actions.</p>{entities.map(entity => <div key={entity.id} className="flex items-center justify-between gap-2"><span className="min-w-0 break-words">{entity.name}</span><button onClick={() => void remove(entity)} disabled={!!busy} className="text-red-600 hover:underline" aria-label={`Delete group ${entity.name}`}>{busy === entity.id ? "Deleting…" : "Delete"}</button></div>)}</div>}
    {error && <p role="alert" className="mt-2 text-red-600">{error}</p>}
  </div>;
}
