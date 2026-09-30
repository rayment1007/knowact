import { useState, type FormEvent } from "react";
import { documentsApi, getErrorMessage, sourceItemsApi } from "@/api";
import type { Sensitivity } from "@/api";
import { ErrorState } from "@/components/feedback";
import { inputClass, primaryClass, WorkspaceDialog } from "./WorkspacePrimitives";
import type { ItemKind } from "@/api/workspaceBrowser";

export default function AddSourceDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (kind: ItemKind, id: string) => void }) {
  const [mode, setMode] = useState<"note" | "file">("note");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sensitivity, setSensitivity] = useState<Sensitivity>("INTERNAL");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    try {
      if (mode === "note") {
        const source = await sourceItemsApi.create({ source_type: "MANUAL", title: title.trim(), content: content.trim() });
        onCreated("source", source.id);
      } else if (file) {
        const source = await documentsApi.upload(file, sensitivity);
        onCreated("file", source.id);
      }
    } catch (err) { setError(getErrorMessage(err)); }
    finally { setBusy(false); }
  }
  return <WorkspaceDialog title="Add source" onClose={onClose} busy={busy}>
    <div className="mb-5 flex gap-2" role="group" aria-label="Source input method">{([['note', 'Manual note'], ['file', 'Upload file']] as const).map(([value, label]) => <button type="button" key={value} disabled={busy} aria-pressed={mode === value} onClick={() => { setMode(value); setError(""); }} className={`rounded-lg px-4 py-2 text-sm font-medium ${mode === value ? "bg-blue-50 text-blue-700" : "text-slate-500 hover:bg-slate-50"}`}>{label}</button>)}</div>
    <form className="space-y-4" onSubmit={submit}>
      {mode === "note" ? <>
        <p className="text-sm text-slate-500">Capture a meeting note, project update, or pasted text. It stays an original source until you choose to analyse it.</p>
        <label className="block text-sm font-medium text-slate-700">Title<input autoFocus required maxLength={512} className={`${inputClass} mt-1`} value={title} onChange={event => setTitle(event.target.value)} placeholder="What is this note about?" /></label>
        <label className="block text-sm font-medium text-slate-700">Content<textarea required rows={7} className={`${inputClass} mt-1`} value={content} onChange={event => setContent(event.target.value)} placeholder="Add the original information and any useful context…" /></label>
      </> : <>
        <p className="text-sm text-slate-500">Upload a PDF, DOCX, or TXT file, up to 20 MB. After uploading, choose Process file in its details to extract searchable text. Uploading alone does not create confirmed knowledge.</p>
        <label className="block rounded-xl border-2 border-dashed border-blue-200 bg-blue-50/30 p-5 text-sm font-medium text-slate-700">Choose a file<input required type="file" accept=".pdf,.docx,.txt" onChange={event => setFile(event.target.files?.[0] ?? null)} className="mt-3 block w-full text-sm file:mr-3 file:rounded-md file:border-0 file:bg-blue-100 file:px-3 file:py-2 file:text-blue-700" /></label>
        <label className="block text-sm font-medium text-slate-700">Sensitivity<select className={`${inputClass} mt-1`} value={sensitivity} onChange={event => setSensitivity(event.target.value as Sensitivity)}>{["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_SENSITIVE"].map(value => <option key={value}>{value}</option>)}</select></label>
      </>}
      {error && <ErrorState message={error} variant="alert" />}
      <div className="flex justify-end"><button className={primaryClass} disabled={busy || (mode === "note" ? !title.trim() || !content.trim() : !file)}>{busy ? "Adding…" : mode === "note" ? "Add note" : "Upload file"}</button></div>
    </form>
  </WorkspaceDialog>;
}
