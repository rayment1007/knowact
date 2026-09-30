import { OriginLinks } from "./SourceProposal";
import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { actionsApi, getErrorMessage, gmailApi, type ActionItem, type ActionStatus, type EmailTaskSuggestion } from "@/api";
import { browserApi } from "@/api/workspaceBrowser";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import AddToCalendar from "@/components/AddToCalendar";
import EvidenceBadge from "@/components/EvidenceBadge";
import { ErrorState, LoadingState } from "@/components/feedback";
import { buttonClass, inputClass, primaryClass, StateBadge, WorkspaceDialog } from "./WorkspacePrimitives";

function ActionEditor({ item, onClose }: { item?: ActionItem; onClose: () => void }) {
  const [title, setTitle] = useState(item?.title ?? "");
  const [description, setDescription] = useState(item?.description ?? "");
  const [due, setDue] = useState(item?.due_date ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try {
      const payload = { title: title.trim(), description: description.trim(), due_date: due || null };
      if (item) await actionsApi.update(item.id, payload);
      else await actionsApi.create({ ...payload, due_date: due || undefined });
      onClose();
    } catch (err) { setError(getErrorMessage(err)); } finally { setBusy(false); }
  }
  return <WorkspaceDialog title={item ? "Edit action" : "Add action"} onClose={onClose} busy={busy}>
    <form onSubmit={save} className="space-y-4">
      <label className="block text-sm font-medium text-slate-700">Title<input autoFocus required maxLength={512} className={`${inputClass} mt-1`} value={title} onChange={e => setTitle(e.target.value)} /></label>
      <label className="block text-sm font-medium text-slate-700">Description<textarea rows={4} className={`${inputClass} mt-1`} value={description} onChange={e => setDescription(e.target.value)} /></label>
      <label className="block text-sm font-medium text-slate-700">Due date<input type="date" className={`${inputClass} mt-1`} value={due} onChange={e => setDue(e.target.value)} /></label>
      {error && <ErrorState message={error} variant="alert" />}
      <div className="flex justify-end"><button disabled={busy || !title.trim()} className={primaryClass}>{busy ? "Saving…" : "Save action"}</button></div>
    </form>
  </WorkspaceDialog>;
}
export function AddActionDialog({ onClose }: { onClose: () => void }) { return <ActionEditor onClose={onClose} />; }

function ActionContents({ item, onDeleted }: { item: ActionItem; onDeleted: () => void }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const context = useCachedQuery(`/workspace/actions/${item.id}/context`, () => browserApi.actionContext(item.id));
  async function change(status: ActionStatus) {
    if (busy) return;
    setBusy(true); setError("");
    try { await actionsApi.update(item.id, { status }); } catch (err) { setError(getErrorMessage(err)); } finally { setBusy(false); }
  }
  async function remove() {
    if (busy || !window.confirm(`Permanently delete "${item.title}"? Any Google Calendar event linked to it is also removed. This cannot be undone.`)) return;
    setBusy(true); setError("");
    try { await actionsApi.remove(item.id); onDeleted(); } catch (err) { setError(getErrorMessage(err)); } finally { setBusy(false); }
  }
  return <div className="space-y-5 p-5">
    <div className="flex flex-wrap items-center gap-2"><StateBadge status={item.status} /><span className="text-xs text-slate-500">{item.ai_generated ? "AI-assisted action" : "Created by you"}</span></div>
    <h2 className="break-words text-lg font-semibold text-slate-900">{item.title}</h2>
    <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-600">{item.description || "No description."}</p>
    <p className="text-sm text-slate-600">Due: <strong className="font-medium">{item.due_date ? new Date(`${item.due_date}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "No due date"}</strong></p>
    <EvidenceBadge text={item.evidence_text ?? undefined} />
    {item.knowledge_item_id && <Link className="block text-sm font-medium text-blue-700 hover:underline" to={`/workspace/knowledge?item=knowledge:${item.knowledge_item_id}`}>View linked knowledge →</Link>}
    {context.data && <OriginLinks origins={context.data.origins ?? []} />}
    <div className="flex flex-wrap gap-2"><button className={buttonClass} disabled={busy} onClick={() => setEditing(true)}>Edit</button>
      {item.status !== "DONE" && <button className={primaryClass} disabled={busy} onClick={() => void change("DONE")}>Mark done</button>}
      {item.status !== "OPEN" && <button className={buttonClass} disabled={busy} onClick={() => void change("OPEN")}>Reopen</button>}
      {item.status === "OPEN" && <button className={buttonClass} disabled={busy} onClick={() => void change("IN_PROGRESS")}>Start task</button>}
      {!["DONE", "CANCELLED"].includes(item.status) && <button className={buttonClass} disabled={busy} onClick={() => void change("CANCELLED")}>Cancel task</button>}
    </div>
    {error && <ErrorState message={error} variant="alert" />}
    <section className="space-y-3 rounded-xl border border-slate-200 p-4"><h3 className="text-sm font-semibold text-slate-900">Google Calendar</h3><p className="text-xs leading-relaxed text-slate-500">Completing or accepting a task does not change Google Calendar. Review and approve a calendar operation separately.</p>
      {context.error ? <ErrorState message="Could not check existing calendar links." onRetry={context.retry} /> : !context.data ? <LoadingState label="Checking calendar links…" /> : context.data.calendar_links.length ? context.data.calendar_links.map(link => <AddToCalendar key={link.id} actionId={item.id} link={link} onChange={() => {}} />) : <AddToCalendar actionId={item.id} link={null} onChange={() => {}} />}
    </section>
    <button disabled={busy} onClick={() => void remove()} className="text-xs text-rose-600 hover:underline">Delete permanently</button>
    {editing && <ActionEditor item={item} onClose={() => setEditing(false)} />}
  </div>;
}

function SuggestionContents({ item, onAccepted }: { item: EmailTaskSuggestion; onAccepted: () => void }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState(item.description ?? "");
  const [due, setDue] = useState(item.suggested_due_date ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(operation: "save" | "accept" | "reject") {
    if (busy) return;
    setBusy(true); setError("");
    try {
      if (operation === "save") { await gmailApi.editSuggestion(item.id, { title: title.trim(), description: description.trim(), suggested_due_date: due || null }); setEditing(false); }
      else { if (operation === "accept") await gmailApi.confirmSuggestion(item.id); else await gmailApi.rejectSuggestion(item.id); onAccepted(); }
    } catch (err) { setError(getErrorMessage(err)); } finally { setBusy(false); }
  }
  return <div className="space-y-5 p-5"><StateBadge status={item.status} /><h2 className="text-lg font-semibold text-slate-900">Action suggestion</h2>
    <p className="text-xs leading-relaxed text-slate-500">Review the evidence before adding this suggestion to your action list. Accepting it does not send email or change Google Calendar.</p>
    {editing ? <form className="space-y-3" onSubmit={event => { event.preventDefault(); void run("save"); }}>
      <label className="block text-sm">Title<input required className={`${inputClass} mt-1`} value={title} onChange={e => setTitle(e.target.value)} /></label>
      <label className="block text-sm">Description<textarea rows={4} className={`${inputClass} mt-1`} value={description} onChange={e => setDescription(e.target.value)} /></label>
      <label className="block text-sm">Due date<input type="date" className={`${inputClass} mt-1`} value={due} onChange={e => setDue(e.target.value)} /></label>
      <button disabled={busy || !title.trim()} className={primaryClass}>Save suggestion</button><button type="button" disabled={busy} className={`${buttonClass} ml-2`} onClick={() => setEditing(false)}>Cancel edit</button>
    </form> : <><h3 className="text-base font-medium text-slate-900">{item.title}</h3><p className="whitespace-pre-wrap text-sm text-slate-600">{item.description}</p><p className="text-sm text-slate-500">Suggested due date: {item.suggested_due_date ?? "Not specified"}</p></>}
    <EvidenceBadge text={item.evidence_text ?? undefined} />
    <Link className="block text-sm font-medium text-blue-700" to={`/workspace/sources?item=email:${item.email_message_record_id}`}>View original email →</Link>
    {!editing && item.status === "SUGGESTED" && <div className="flex flex-wrap gap-2"><button disabled={busy} className={primaryClass} onClick={() => void run("accept")}>Accept into actions</button><button disabled={busy} className={buttonClass} onClick={() => setEditing(true)}>Edit suggestion</button><button disabled={busy} className={buttonClass} onClick={() => void run("reject")}>Reject</button></div>}
    {error && <ErrorState message={error} variant="alert" />}
  </div>;
}

export default function ActionDetail({ id, onDeleted }: { id: string; onDeleted: () => void }) {
  const query = useCachedQuery(`/actions/${id}`, () => actionsApi.get(id));
  if (query.error) return <ErrorState className="m-5" message="Could not load this action." onRetry={query.retry} />;
  return query.data ? <ActionContents item={query.data} onDeleted={onDeleted} /> : <LoadingState className="p-5" label="Loading action…" />;
}
export function SuggestionDetail({ id, onAccepted }: { id: string; onAccepted: () => void }) {
  const query = useCachedQuery(`/workspace/suggestions/${id}`, () => browserApi.suggestion(id));
  if (query.error) return <ErrorState className="m-5" message="Could not load this suggestion." onRetry={query.retry} />;
  return query.data ? <SuggestionContents item={query.data} onAccepted={onAccepted} /> : <LoadingState className="p-5" label="Loading suggestion…" />;
}
