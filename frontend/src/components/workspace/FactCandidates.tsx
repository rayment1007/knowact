import { useRef, useState } from "react";
import { getErrorMessage } from "@/api";
import { browserApi, type FactCandidate, type FactExtraction } from "@/api/workspaceBrowser";
import { ErrorState } from "@/components/feedback";
import { buttonClass, inputClass } from "./WorkspacePrimitives";
import FactReview from "./FactReview";

const modalityLabels: Record<FactCandidate["modality"], string> = {
  ASSERTION: "Statement", PROPOSED_CHANGE: "Proposed change", QUESTION: "Question", UNCERTAIN: "Uncertain",
};

function Candidate({ candidate, disabled, onReload, onBusyChange }: {
  candidate: FactCandidate; disabled: boolean; onReload: () => Promise<void>; onBusyChange: (busy: boolean) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [value, setValue] = useState(candidate.value_date ?? "");
  const [excerpt, setExcerpt] = useState(candidate.evidence_text ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const request = useRef<{ payload: string; key: string } | null>(null);
  const inFlight = useRef(false);
  const canEdit = candidate.outcome === "PROPOSED" && !!candidate.proposal_id && !!candidate.evidence_text && candidate.proposal_revision !== null
    && ["PENDING", "UNRESOLVED"].includes(candidate.proposal_status ?? "") && candidate.resolved_status !== "APPROVED";
  const effectiveStatus = candidate.resolved_status ?? candidate.proposal_status;

  async function save() {
    if (inFlight.current || disabled || !candidate.proposal_id || candidate.proposal_revision === null) return;
    const payload = JSON.stringify([candidate.proposal_revision, value, excerpt]);
    if (request.current?.payload !== payload) request.current = { payload, key: crypto.randomUUID() };
    inFlight.current = true; setBusy(true); onBusyChange(true); setError(""); setSaved(false);
    try {
      await browserApi.editFact(candidate.proposal_id, candidate.proposal_revision, value, excerpt, request.current.key);
      await onReload();
      setEditing(false); setSaved(true);
    } catch (err) { setError(getErrorMessage(err)); }
    finally { inFlight.current = false; setBusy(false); onBusyChange(false); }
  }

  return <article className="space-y-3 rounded-xl border border-slate-200 p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-medium text-slate-900">{candidate.subject}</h4><span className="rounded-full bg-slate-100 px-2 py-1 text-xs text-slate-600">{modalityLabels[candidate.modality]}</span></div>
    {candidate.occurrence && <p className="text-xs text-slate-500">Occurrence: {candidate.occurrence}</p>}
    <p className="text-sm text-slate-700">Proposed date: <strong>{candidate.value_date ?? "Needs clarification"}</strong></p>
    {candidate.previous_value_date && <p className="text-xs text-slate-500">Earlier date mentioned: {candidate.previous_value_date}</p>}
    {candidate.source_context !== "DIRECT" && <p className="text-xs text-amber-800">This passage is {candidate.source_context.toLowerCase()} content. Its context needs review.</p>}
    <blockquote className="whitespace-pre-wrap break-words rounded-lg bg-slate-50 p-3 text-sm text-slate-600">{candidate.evidence_text}</blockquote>
    <p className="text-xs text-slate-500">{effectiveStatus === "APPROVED" ? "Approved fact." : effectiveStatus === "REJECTED" ? "Rejected candidate." : effectiveStatus === "UNRESOLVED" ? "Unresolved · current fact unchanged." : candidate.outcome === "PROPOSED" ? "Unconfirmed · awaiting comparison and review." : "Needs clarification before this can become a fact."}</p>
    {!!candidate.reason_codes.length && <ul className="list-inside list-disc text-xs text-amber-800">{candidate.reason_codes.map((reason, index) => <li key={`${reason}:${index}`}>{reason.replace(/_/g, " ").toLowerCase()}</li>)}</ul>}
    {candidate.proposal_id && !editing && <button type="button" className={buttonClass} disabled={disabled || busy} aria-expanded={reviewing} onClick={() => setReviewing(!reviewing)}>{reviewing ? "Close fact review" : effectiveStatus === "APPROVED" ? "View fact review" : "Review fact"}</button>}
    {canEdit && !editing && !reviewing && <button type="button" className={buttonClass} disabled={disabled} onClick={() => { setValue(candidate.value_date ?? ""); setExcerpt(candidate.evidence_text ?? ""); setEditing(true); setSaved(false); }}>Edit candidate</button>}
    {reviewing && candidate.proposal_id && <div className="border-t border-slate-200 pt-4"><FactReview key={candidate.active_resolution_id ?? candidate.proposal_id} id={candidate.active_resolution_id ?? candidate.proposal_id} onChanged={onReload} onBusyChange={onBusyChange} /></div>}
    {editing && <fieldset disabled={busy || disabled} className="space-y-3 border-t border-slate-100 pt-3" onKeyDown={event => { if (event.key === "Enter" && event.target instanceof HTMLInputElement) event.preventDefault(); }}>
      <label className="block text-sm font-medium text-slate-700">Candidate date<input type="date" className={`${inputClass} mt-1`} value={value} onChange={e => setValue(e.target.value)} /></label>
      <label className="block text-sm font-medium text-slate-700">Exact supporting passage<textarea rows={4} maxLength={4000} className={`${inputClass} mt-1`} value={excerpt} onChange={e => setExcerpt(e.target.value)} /></label>
      <p className="text-xs text-slate-500">Use an exact passage that contains the complete date. Saving an edit keeps this candidate unconfirmed.</p>
      <div className="flex gap-2"><button type="button" className={buttonClass} disabled={!value || !excerpt.trim()} onClick={() => void save()}>{busy ? "Saving candidate…" : "Save candidate"}</button><button type="button" className={buttonClass} onClick={() => { setEditing(false); setValue(candidate.value_date ?? ""); setExcerpt(candidate.evidence_text ?? ""); }}>Cancel edit</button></div>
    </fieldset>}
    {saved && <p role="status" className="text-xs text-emerald-700">Candidate saved. It remains unconfirmed.</p>}
    {error && <ErrorState message={error} variant="alert" />}
  </article>;
}

export default function FactCandidates({ extraction, disabled = false, onReload, onBusyChange }: {
  extraction: FactExtraction; disabled?: boolean; onReload: () => Promise<void>; onBusyChange: (busy: boolean) => void;
}) {
  if (extraction.redacted_at) return <section aria-label="Extracted meeting-date candidates" className="space-y-2 border-t border-slate-200 pt-5"><h3 className="font-semibold text-slate-900">Meeting-date facts</h3>{extraction.project_name && <p className="text-xs text-slate-500">Project: {extraction.project_name}</p>}<p role="status" className="text-sm text-slate-600">Evidence removed. These extracted details are no longer available.</p></section>;
  const coverage = extraction.coverage;
  return <section aria-label="Extracted meeting-date candidates" className="space-y-3 border-t border-slate-200 pt-5">
    <h3 className="font-semibold text-slate-900">Meeting-date facts</h3>
    {extraction.project_name && <p className="text-xs text-slate-500">Project: {extraction.project_name}</p>}
    <p className="text-xs leading-relaxed text-slate-500">These candidates are reviewed separately. Approving the summary does not approve any fact.</p>
    <div className={`rounded-lg p-3 text-xs ${coverage.partial ? "bg-amber-50 text-amber-800" : "bg-slate-50 text-slate-600"}`}>
      <p>{coverage.partial ? "Partial analysis. " : ""}Analysed {coverage.analyzed_characters.toLocaleString()} of {coverage.total_characters.toLocaleString()} source characters.</p>
      {coverage.partial && <p className="mt-1">Included character ranges: {coverage.ranges.map(range => `${range.start + 1}–${range.end}`).join(", ")}. Check the original for omitted content.</p>}
    </div>
    {extraction.warnings.map((warning, index) => <p key={`${warning}:${index}`} role="status" className="text-xs text-amber-800">{warning}</p>)}
    {!extraction.candidates.length && <p className="text-sm text-slate-600">No supported meeting-date facts were found in the analysed content. You can still save the general summary.</p>}
    {extraction.candidates.map((candidate, index) => <Candidate key={candidate.proposal_id ?? index} candidate={candidate} disabled={disabled} onReload={onReload} onBusyChange={onBusyChange} />)}
  </section>;
}
