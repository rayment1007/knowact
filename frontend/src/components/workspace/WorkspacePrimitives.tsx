import { useEffect, useRef, type ReactNode } from "react";
import WorkspaceIcon from "@/components/WorkspaceIcon";

export const buttonClass = "inline-flex items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:opacity-50";
export const primaryClass = "inline-flex items-center justify-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 disabled:opacity-50";
export const inputClass = "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500";
const labels: Record<string, string> = { CREATED_HERE: "Created in KnowAct",
  NEEDS_REVIEW: "Review classification", SUGGESTED: "Needs review", RAW: "Original source",
  REVIEWED: "Classification reviewed", UPLOADED: "Ready to process", PROCESSING: "Processing", FAILED: "Processing failed",
  INDEXED: "Searchable", OPEN: "Open", IN_PROGRESS: "In progress", DONE: "Done", CANCELLED: "Cancelled",
  CONFIRMED: "Confirmed", REJECTED: "Rejected", ARCHIVED: "Archived", DISMISSED: "Dismissed",
};
export const stateLabel = (state: string) => labels[state] ?? state;
export function StateBadge({ status }: { status: string }) {
  const review = ["NEEDS_REVIEW", "SUGGESTED"].includes(status);
  const color = review || status === "FAILED" ? "bg-rose-50 text-rose-700" : ["CONFIRMED", "DONE", "INDEXED", "REVIEWED"].includes(status) ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600";
  return <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-medium ${color}`}>{review && <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-rose-500" />}{stateLabel(status)}</span>;
}
export function Pagination({ total, page, size, onPage }: { total: number; page: number; size: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / size));
  const visible = Array.from(new Set([1, ...Array.from({ length: 3 }, (_, i) => page - 1 + i).filter(n => n > 1 && n < pages), pages])).sort((a, b) => a - b);
  return <nav aria-label="Pagination" className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
    <span>{total ? `${(page - 1) * size + 1}–${Math.min(page * size, total)} of ${total}` : "0 items"}</span>
    <div className="flex items-center gap-1">
      <button className="rounded px-2 py-1.5 hover:bg-slate-100 disabled:opacity-40" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      {visible.map((number, index) => <span key={number} className="inline-flex items-center">{index > 0 && number > visible[index - 1] + 1 && <span className="px-1">…</span>}<button aria-label={`Page ${number}`} aria-current={number === page ? "page" : undefined} className={`min-w-7 rounded px-2 py-1.5 ${number === page ? "bg-blue-600 font-semibold text-white" : "hover:bg-slate-100"}`} onClick={() => onPage(number)}>{number}</button></span>)}
      <button className="rounded px-2 py-1.5 hover:bg-slate-100 disabled:opacity-40" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  </nav>;
}
export function WorkspaceDialog({ title, onClose, children, busy = false }: { title: string; onClose: () => void; children: ReactNode; busy?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const node = dialog.current;
    node?.showModal();
    return () => { node?.close(); previous?.focus(); };
  }, []);
  return <dialog ref={dialog} aria-label={title} onCancel={event => { event.preventDefault(); if (!busy) onClose(); }} className="m-auto max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-2xl overflow-y-auto rounded-2xl border-0 bg-white p-0 shadow-2xl backdrop:bg-slate-900/40">
    <header className="flex items-center justify-between border-b border-slate-100 px-6 py-4"><h2 className="text-lg font-semibold text-slate-900">{title}</h2><button disabled={busy} aria-label={`Close ${title}`} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 disabled:opacity-50" onClick={onClose}><WorkspaceIcon name="close" /></button></header>
    <div className="p-6">{children}</div>
  </dialog>;
}
