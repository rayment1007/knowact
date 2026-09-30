import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { documentsApi, gmailApi, getErrorMessage } from "@/api";
import { browserApi, itemLink, type SourceKind, type SourceDetail as SourceDetailData } from "@/api/workspaceBrowser";
import { invalidateApiCache } from "@/api/cache";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import { SourceItemCard } from "@/pages/SourceInboxPage";
import { ErrorState, LoadingState } from "@/components/feedback";
import { formatDay, formatTimestamp } from "@/utils/workspaceDates";
import { Pagination, primaryClass, StateBadge } from "./WorkspacePrimitives";

import { SourceAddButtons } from "./SourceProposal";

function RelatedItems({ kind, id }: { kind: SourceKind; id: string }) {
  const [page, setPage] = useState(1);
  const offset = (page - 1) * 10;
  const query = useCachedQuery(`/workspace/source/${kind}/${id}/links?offset=${offset}&limit=10`, () => browserApi.links(kind, id, offset));
  if (query.error) return <ErrorState message="Could not load linked items." onRetry={query.retry} />;
  if (!query.data) return <LoadingState label="Loading linked items…" />;
  return <section className="space-y-3">
    <h3 className="text-sm font-semibold text-slate-900">Linked knowledge & actions <span className="ml-1 text-slate-400">{query.data.total}</span></h3>
    {!query.data.total ? <p className="rounded-lg bg-slate-50 p-4 text-sm leading-relaxed text-slate-500">Only the original source is available here. No knowledge or action is linked to it yet.</p> : <>
      {query.data.items.map(item => <Link key={`${item.kind}:${item.id}`} to={itemLink(item)} className="block rounded-xl border border-slate-200 p-3 transition hover:border-blue-300 hover:bg-blue-50/30">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-medium text-blue-700">{item.kind === "proposal" ? `${item.source_type === "knowledge" ? "Knowledge" : "Action"} draft` : item.kind === "knowledge" ? "Extracted knowledge" : item.kind === "suggestion" ? "Action suggestion" : "Action"}</span><StateBadge status={item.status} /></div>
        <p className="text-sm font-medium text-slate-800">{item.title}</p><p className="mt-1 line-clamp-2 text-xs text-slate-500">{item.excerpt}</p>
      </Link>)}
      {query.data.total > 10 && <Pagination total={query.data.total} size={10} page={page} onPage={setPage} />}
    </>}
  </section>;
}

function FileText({ id }: { id: string }) {
  const [page, setPage] = useState(1);
  const offset = (page - 1) * 10;
  const query = useCachedQuery(`/workspace/files/${id}/chunks?offset=${offset}&limit=10`, () => browserApi.chunks(id, offset));
  if (query.error) return <ErrorState message="Could not load the extracted file text." onRetry={query.retry} />;
  if (!query.data) return <LoadingState label="Loading file text…" />;
  return <div><p className="mb-4 text-xs text-slate-500">Extracted text passages; formatting can differ from the original file. Passages may overlap.</p>
    {!query.data.total && <p className="text-sm text-slate-500">No extracted text yet. Process the file from Overview first.</p>}
    {query.data.items.map(chunk => <section key={chunk.id} className="mb-4 rounded-lg border border-slate-100 p-3"><h3 className="mb-2 text-xs text-slate-400">Passage {chunk.chunk_index + 1}</h3><p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-700">{chunk.text}</p></section>)}
    {query.data.total > 10 && <Pagination total={query.data.total} size={10} page={page} onPage={setPage} />}
  </div>;
}

function SourceContents({ detail, onDeleted }: { detail: SourceDetailData; onDeleted: () => void }) {
  const [tab, setTab] = useState("overview");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const unchanged = useCallback(() => {}, []);
  const { kind, id, source, document, metadata } = detail;
  async function processFile() {
    if (busy) return;
    setBusy(true); setError("");
    try { await documentsApi.process(id); }
    catch (err) {
      setError(getErrorMessage(err));
      // Failed processing persists FAILED even though the POST returns an error.
      invalidateApiCache(["/documents", "/workspace/source", "/workspace/items", "/workspace/counts", "/workspace/files", "/workspace/summary"]);
    } finally { setBusy(false); }
  }
  async function remove() {
    const message = kind === "file" ? `Permanently delete "${detail.title}"? This removes the file, its extracted chunks, and embeddings. This cannot be undone.` : `Permanently delete "${detail.title}" and its email task suggestions? This cannot be undone.`;
    if (busy || !window.confirm(message)) return;
    setBusy(true); setError("");
    try { if (kind === "file") await documentsApi.remove(id); else await gmailApi.removeMessage(id); onDeleted(); }
    catch (err) { setError(getErrorMessage(err)); } finally { setBusy(false); }
  }
  const external = kind === "email" && metadata.gmail_message_id
    ? `https://mail.google.com/mail/u/0/#all/${encodeURIComponent(metadata.gmail_message_id)}`
    : metadata.html_link && /^https:\/\/(calendar\.google\.com|www\.google\.com)\//.test(metadata.html_link) ? metadata.html_link : null;
  return <>
    <header className="space-y-2 px-5 pt-5">
      <h2 className="break-words text-lg font-semibold text-slate-900">{detail.title}</h2>
      {metadata.sender && <p className="break-words text-xs text-slate-500">From: {metadata.sender}</p>}
      {!!metadata.recipients?.length && <p className="break-words text-xs text-slate-500">To: {metadata.recipients.join(", ")}</p>}
      {metadata.received_at && <p className="text-xs text-slate-500">Received: {formatTimestamp(metadata.received_at)}</p>}
      {external && <a href={external} target="_blank" rel="noreferrer" className="inline-block text-xs font-medium text-blue-700 hover:underline">Open in {kind === "email" ? "Gmail" : "Google Calendar"} ↗</a>}
    </header>
    <div className="mt-4 flex gap-4 overflow-x-auto border-b border-slate-100 px-5" role="group" aria-label="Source detail views">
      {[['overview', 'Overview'], ['analysis', 'AI review'], ['raw', 'Raw content']].map(([value, label]) => <button key={value} aria-pressed={tab === value} onClick={() => setTab(value)} className={`whitespace-nowrap border-b-2 py-3 text-xs font-medium ${tab === value ? "border-blue-600 text-blue-700" : "border-transparent text-slate-500"}`}>{label}</button>)}
    </div>
    <div className="space-y-5 p-5">
      <SourceAddButtons kind={kind} id={id} />
      {error && <ErrorState message={error} variant="alert" />}
      {tab === "overview" && <>
        {document && <div className="space-y-3 rounded-xl bg-slate-50 p-4">
          <div className="flex flex-wrap gap-2"><StateBadge status={document.processing_status} /><span className="text-xs text-slate-500">{document.sensitivity}</span></div>
          <p className="text-xs leading-relaxed text-slate-500">Processing extracts text for search. It does not approve knowledge or create actions.</p>
          {document.failure_reason && <p className="text-sm text-rose-700">{document.failure_reason}</p>}
          {["UPLOADED", "FAILED"].includes(document.processing_status) && <button disabled={busy} onClick={() => void processFile()} className={primaryClass}>{busy ? "Processing…" : "Process file"}</button>}
        </div>}
        {kind === "calendar" && <dl className="space-y-2 rounded-xl bg-slate-50 p-4 text-sm"><div><dt className="text-xs text-slate-500">Starts</dt><dd>{metadata.starts_at ? (metadata.starts_at.length === 10 ? formatDay(metadata.starts_at) + " (all day)" : formatTimestamp(metadata.starts_at)) : "Not recorded"}</dd></div><div><dt className="text-xs text-slate-500">Ends</dt><dd>{metadata.ends_at ? (metadata.ends_at.length === 10 ? formatDay(metadata.ends_at) + " (exclusive end date)" : formatTimestamp(metadata.ends_at)) : "Not recorded"}</dd></div>{metadata.location && <div><dt className="text-xs text-slate-500">Location</dt><dd>{metadata.location}</dd></div>}</dl>}
        {detail.raw_content && <section><h3 className="mb-2 text-sm font-semibold text-slate-900">Original preview</h3><p className="line-clamp-4 whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-600">{detail.raw_content}</p><button onClick={() => setTab("raw")} className="mt-2 text-xs font-medium text-blue-700">Read raw content →</button></section>}
        <RelatedItems kind={kind} id={id} />
        {(kind === "file" || kind === "email") && <button disabled={busy} onClick={() => void remove()} className="text-xs text-rose-600 hover:underline">Delete permanently</button>}
      </>}
      {tab === "analysis" && (source ? <><p className="text-xs leading-relaxed text-slate-500">These are the existing AI classification and extraction tools. You can also use Add to Knowledge or Add to Actions above to choose a destination yourself.</p><ul><SourceItemCard key={source.id} item={source} onItemChanged={unchanged} onDeleted={onDeleted} initiallyExpanded panel /></ul></> : <div className="rounded-xl bg-slate-50 p-4 text-sm leading-relaxed text-slate-500">No AI summary or classification is stored for this source. {kind === "file" ? "File processing makes its text searchable; it does not create a reviewed knowledge record." : kind === "calendar" ? "Use Add to Knowledge or Add to Actions to prepare a draft from this captured event." : "The original email text is not stored here."}</div>)}
      {tab === "raw" && (kind === "file" ? <FileText id={id} /> : <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-700">{detail.raw_content || "No raw text is stored for this source."}</p>)}
    </div>
  </>;
}

export default function SourceDetail({ kind, id, onDeleted }: { kind: SourceKind; id: string; onDeleted: () => void }) {
  const query = useCachedQuery(`/workspace/source/${kind}/${id}`, () => browserApi.source(kind, id));
  if (query.error) return <ErrorState className="m-5" message="This source could not be loaded. It may have been removed or is no longer accessible." onRetry={query.retry} />;
  if (!query.data) return <LoadingState className="p-5" label="Loading source…" />;
  return <SourceContents detail={query.data} onDeleted={onDeleted} />;
}
