import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, businessEntitiesApi, getErrorMessage } from "@/api";
import { FACT_PATH, factMemoryApi, type FactEvidence, type FactProposalDetail, type FactRelationship, type FactVersion } from "@/api/factMemory";
import { itemLink } from "@/api/workspaceBrowser";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import { ErrorState, LoadingState } from "@/components/feedback";
import { formatTimestamp } from "@/utils/workspaceDates";
import { buttonClass, inputClass, Pagination, primaryClass } from "./WorkspacePrimitives";

const relationshipLabels: Record<FactRelationship, string> = { NEW: "New fact", SAME: "Same value", REVISION: "Revision", CONFLICT: "Conflict", UNCLEAR: "Needs clarification" };
export function FactRelationshipBadge({ relationship }: { relationship: FactRelationship }) {
  return <span className={`rounded-full px-2 py-1 text-xs font-medium ${relationship === "CONFLICT" ? "bg-rose-50 text-rose-700" : relationship === "UNCLEAR" ? "bg-amber-50 text-amber-800" : "bg-blue-50 text-blue-700"}`}>{relationshipLabels[relationship]}</span>;
}

export function FactEvidenceCard({ evidence }: { evidence: FactEvidence }) {
  const { source } = evidence;
  const removed = !!evidence.redacted_at || !!source.purged_at;
  return <div className="space-y-2 rounded-lg border border-slate-200 bg-white p-3 text-xs">
    <p className="font-medium text-slate-800">{removed ? "Evidence removed" : source.source_title || "Untitled source"}</p>
    {!removed && <blockquote className="whitespace-pre-wrap break-words text-slate-600">{evidence.excerpt}</blockquote>}
    {!removed && <p className="text-slate-500">Source date: {source.source_time ? formatTimestamp(source.source_time) : "Unknown"}</p>}
    {!removed && source.available ? <Link className="font-medium text-blue-700 hover:underline" to={itemLink({ kind: source.source_kind, id: source.source_id })}>Open original source →</Link> : <p className="text-slate-500">Original source unavailable.</p>}
  </div>;
}

export default function FactReview({ id, onChanged, onBusyChange }: { id: string; onChanged?: () => void | Promise<void>; onBusyChange?: (busy: boolean) => void }) {
  const [activeId, setActiveId] = useState(id);
  const query = useCachedQuery(`${FACT_PATH}/proposals/${activeId}`, () => factMemoryApi.detail(activeId));
  if (query.error && !query.data) return <ErrorState message="Could not load this fact for review." onRetry={query.retry} />;
  if (!query.data) return <LoadingState label="Loading fact review…" />;
  return <>{query.error && <ErrorState message="Could not refresh this review. Your open edits are preserved." onRetry={query.retry} />}<FactReviewEditor key={`${activeId}:${query.data.proposal.redacted_at ?? query.data.evidence.redacted_at ?? "available"}`} initial={query.data} onSelect={setActiveId} onChanged={onChanged} onBusyChange={onBusyChange} /></>;
}

function FactReviewEditor({ initial, onSelect, onChanged, onBusyChange }: { initial: FactProposalDetail; onSelect: (id: string) => void; onChanged?: () => void | Promise<void>; onBusyChange?: (busy: boolean) => void }) {
  const [detail, setDetail] = useState(initial);
  const [mapping, setMapping] = useState<FactProposalDetail | null>(initial.proposal.extraction_context?.identity_unverified ? initial : null);
  const [projectId, setProjectId] = useState(initial.identity.project_id);
  const [identityChoice, setIdentityChoice] = useState("");
  const [name, setName] = useState("");
  const [occurrence, setOccurrence] = useState(initial.proposal.extraction_context?.occurrence ?? "");
  const [identitySearch, setIdentitySearch] = useState("");
  const [identityQuery, setIdentityQuery] = useState("");
  const [identityPage, setIdentityPage] = useState(1);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(initial.proposal.value_json?.date ?? "");
  const [excerpt, setExcerpt] = useState(initial.evidence.excerpt ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [notice, setNotice] = useState("");
  const inFlight = useRef(false);
  const requests = useRef(new Map<string, string>());
  const projects = useCachedQuery("/business-entities", businessEntitiesApi.list);
  const identityPath = `${FACT_PATH}/identities?${new URLSearchParams({ project_id: projectId, q: identityQuery, limit: "10", offset: String((identityPage - 1) * 10) })}`;
  const identities = useCachedQuery(identityPath, () => factMemoryApi.identities(identityPath));
  const { proposal, current } = detail;
  const unverified = !!proposal.extraction_context?.identity_unverified;
  const pending = ["PENDING", "UNRESOLVED"].includes(proposal.status);
  const redacted = !!proposal.redacted_at || !!detail.evidence.redacted_at;
  const inactive = !!detail.lineage && (!detail.lineage.active || detail.lineage.candidate_revision !== detail.lineage.candidate_current_revision);
  const dirty = editing && (value !== (proposal.value_json?.date ?? "") || excerpt !== (detail.evidence.excerpt ?? ""));
  const comparison = proposal.comparison_json ?? {};
  const needsCompare = !proposal.comparison_ready || !!proposal.extraction_context?.requires_revalidation || proposal.expected_head_id !== (current?.id ?? null);
  const newerAvailable = initial.proposal.revision !== detail.proposal.revision || initial.current?.id !== detail.current?.id || initial.proposal.status !== detail.proposal.status;

  useEffect(() => {
    if (!dirty && !reason.trim()) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty, reason]);

  function requestKey(operation: string, payload: object) {
    const intent = JSON.stringify([operation, payload]);
    if (!requests.current.has(intent)) requests.current.set(intent, crypto.randomUUID());
    return requests.current.get(intent)!;
  }
  async function perform(action: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); onBusyChange?.(true); setError(""); setNotice("");
    try { await action(); }
    catch (err) { setError(getErrorMessage(err)); if (err instanceof ApiError && err.status === 409) setStale(true); }
    finally { inFlight.current = false; setBusy(false); onBusyChange?.(false); }
  }
  async function reload() {
    await perform(async () => {
      const latest = await factMemoryApi.detail(proposal.id, true);
      setDetail(latest); setStale(false);
      if (mapping) {
        const candidateId = latest.lineage?.candidate_id ?? latest.proposal.id;
        setMapping(candidateId === latest.proposal.id ? latest : await factMemoryApi.detail(candidateId, true));
      }
      if (!dirty) { setValue(latest.proposal.value_json?.date ?? ""); setExcerpt(latest.evidence.excerpt ?? ""); }
      setNotice(dirty || reason.trim() ? "Latest saved state loaded. Your unsaved edits and reason were kept; check them before continuing." : "Latest saved state loaded.");
    });
  }
  async function changeIdentity() {
    await perform(async () => {
      const candidateId = detail.lineage?.candidate_id ?? proposal.id;
      const candidate = await factMemoryApi.detail(candidateId, true);
      setMapping(candidate); setProjectId(detail.identity.project_id); setIdentityChoice("");
      setName(""); setOccurrence(detail.identity.occurrence_key); setEditing(false);
    });
  }
  async function resolve() {
    if (!mapping || !identityChoice || !projectId || (identityChoice === "new" && !name.trim())) return;
    await perform(async () => {
      const body = { expected_revision: mapping.proposal.revision, expected_source_fingerprint: mapping.evidence.source.source_fingerprint,
        project_id: projectId, identity_id: identityChoice === "new" ? null : identityChoice,
        canonical_key: identityChoice === "new" ? name.trim() : null, occurrence_key: identityChoice === "new" ? occurrence.trim() : "" };
      const result = await factMemoryApi.resolve(mapping.proposal.id, { ...body, request_key: requestKey("resolve", body) });
      await onChanged?.(); onSelect(result.proposal.id);
    });
  }
  async function compare() {
    await perform(async () => {
      const body = { expected_revision: proposal.revision, expected_head_id: current?.id ?? null, expected_source_fingerprint: detail.evidence.source.source_fingerprint };
      const result = await factMemoryApi.compare(proposal.id, { ...body, request_key: requestKey("compare", body) });
      setDetail(result); setStale(false); setNotice("Comparison updated. The current fact has not changed."); await onChanged?.();
    });
  }
  async function saveEdit() {
    await perform(async () => {
      const body = { expected_revision: proposal.revision, value_date: value, excerpt };
      await factMemoryApi.edit(proposal.id, { ...body, request_key: requestKey("edit", body) });
      const result = await factMemoryApi.detail(proposal.id, true); setDetail(result); setEditing(false); setStale(false);
      setValue(result.proposal.value_json?.date ?? ""); setExcerpt(result.evidence.excerpt ?? "");
      setNotice("Edit saved. Compare this evidence again before approval."); await onChanged?.();
    });
  }
  async function review(decision: "APPROVE" | "REJECT" | "KEEP_UNRESOLVED") {
    await perform(async () => {
      const body = { expected_revision: proposal.revision, expected_head_id: current?.id ?? null,
        expected_source_fingerprint: detail.evidence.source.source_fingerprint, decision,
        ...(decision === "APPROVE" && detail.needs_resolution_reason ? { resolution_reason: reason.trim() } : {}) };
      await factMemoryApi.review(proposal.id, { ...body, request_key: requestKey("review", body) });
      setDetail(await factMemoryApi.detail(proposal.id, true)); setReason(""); setStale(false);
      setNotice(decision === "APPROVE" ? proposal.relationship === "SAME" ? "Evidence linked to the existing version." : "Fact approved. Version history is preserved." : decision === "REJECT" ? "Candidate rejected. The current fact is unchanged." : "Kept unresolved. The current fact is unchanged.");
      await onChanged?.();
    });
  }
  const approveLabel = detail.needs_resolution_reason ? current ? "Approve chosen replacement" : "Approve chosen first value" : proposal.relationship === "SAME" ? "Link supporting evidence" : proposal.relationship === "REVISION" ? "Approve replacement" : "Approve new fact";
  return <section aria-label="Fact review" className="space-y-4" onKeyDown={event => { if (event.key === "Enter" && event.target instanceof HTMLInputElement) event.preventDefault(); }}>
    <div className="flex flex-wrap items-start justify-between gap-2"><div><h3 className="font-semibold text-slate-900">{unverified ? proposal.extraction_context?.subject || "Meeting date candidate" : detail.identity.canonical_key}</h3><p className="mt-1 text-xs text-slate-500">{detail.project_name}{!unverified && detail.identity.occurrence_key ? ` · ${detail.identity.occurrence_key}` : ""}</p></div><FactRelationshipBadge relationship={proposal.relationship} /></div>
    <p className="text-xs text-slate-500">{proposal.status === "APPROVED" ? "Approved" : proposal.status === "REJECTED" ? "Rejected" : proposal.status === "UNRESOLVED" ? "Unresolved · current fact unchanged" : "Awaiting your review"}</p>
    {redacted ? <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-600">Evidence removed. This proposal cannot be approved.</p> : <>
      {detail.active_resolution_id && detail.active_resolution_id !== proposal.id && <div className="rounded-lg bg-blue-50 p-3 text-sm text-blue-800"><p>This candidate has a separate identity review.</p><button type="button" className={`${buttonClass} mt-2`} onClick={() => onSelect(detail.active_resolution_id!)}>Open identity review</button></div>}
      {inactive && <div role="status" className="space-y-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800"><p>This match was replaced or its original candidate changed. Open the candidate to confirm its latest identity.</p><button type="button" className={buttonClass} onClick={() => onSelect(detail.lineage!.candidate_id)}>Open original candidate</button></div>}
      <div className="grid gap-3 sm:grid-cols-2"><div className="rounded-xl bg-slate-50 p-3"><h4 className="text-xs font-semibold text-slate-500">Current accepted date</h4><p className="mt-1 font-semibold text-slate-900">{current?.value_json?.date ?? (current ? "Evidence removed" : "No accepted version")}</p>{current && <p className="mt-1 text-xs text-slate-500">Version {current.version_number} · {formatTimestamp(current.approved_at)}</p>}</div><div className="rounded-xl bg-blue-50 p-3"><h4 className="text-xs font-semibold text-blue-700">Proposed date</h4><p className="mt-1 font-semibold text-slate-900">{proposal.value_json?.date ?? "Needs clarification"}</p></div></div>
      {!!comparison.reasons?.length && <ul className="list-inside list-disc space-y-1 text-sm text-slate-700">{comparison.reasons.map((text, index) => <li key={index}>{text}</li>)}</ul>}
      {!!comparison.warnings?.length && <ul className="list-inside list-disc space-y-1 rounded-lg bg-amber-50 p-3 text-xs text-amber-800">{comparison.warnings.map((text, index) => <li key={index}>{text}</li>)}</ul>}
      {detail.pending_peers_total > 0 && <p className="text-xs text-amber-800">{detail.pending_peers_total} other pending {detail.pending_peers_total === 1 ? "proposal concerns" : "proposals concern"} this fact. Review them before choosing a value.</p>}
      <section className="space-y-2"><h4 className="text-xs font-semibold text-slate-600">Proposed evidence</h4><FactEvidenceCard evidence={detail.evidence} /></section>
      {!!detail.current_evidence.length && <section className="space-y-2"><h4 className="text-xs font-semibold text-slate-600">Current version evidence</h4>{detail.current_evidence.map(row => <FactEvidenceCard key={row.id} evidence={row} />)}{detail.current_evidence_total > detail.current_evidence.length && <p className="text-xs text-slate-500">Showing {detail.current_evidence.length} of {detail.current_evidence_total} evidence passages. Open version history for more.</p>}</section>}
      {mapping && pending && !inactive && !detail.active_resolution_id && <fieldset disabled={busy} className="space-y-3 rounded-xl border border-blue-100 bg-blue-50/30 p-4">
        <h4 className="text-sm font-semibold text-slate-900">Confirm which meeting this describes</h4>
        <p className="text-xs text-slate-600">Choose the same meeting and occurrence to compare dates. A similar name alone does not confirm a match.</p>
        <label className="block text-sm font-medium text-slate-700">Fact project<select className={`${inputClass} mt-1`} value={projectId} onChange={event => { setProjectId(event.target.value); setIdentityChoice(""); setIdentityPage(1); }}>{projects.data?.filter(row => row.entity_type === "PROJECT").map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
        {projects.error && <ErrorState message="Could not load projects." onRetry={projects.retry} />}
        <div className="flex gap-2"><input aria-label="Find an existing meeting" className={inputClass} value={identitySearch} onChange={event => setIdentitySearch(event.target.value)} placeholder="Find an existing meeting" maxLength={160} /><button type="button" className={buttonClass} onClick={() => { setIdentityQuery(identitySearch.trim()); setIdentityPage(1); }}>Find</button></div>
        {identities.error ? <ErrorState message="Could not load meeting identities." onRetry={identities.retry} /> : !identities.data ? <LoadingState label="Loading meeting identities…" /> : <>
          <label className="block text-sm font-medium text-slate-700">Meeting identity<select className={`${inputClass} mt-1`} value={identityChoice} onChange={event => setIdentityChoice(event.target.value)}><option value="">Choose an existing meeting or create one</option>{identities.data.items.map(row => <option key={row.id} value={row.id}>{row.canonical_key}{row.occurrence_key ? ` · ${row.occurrence_key}` : ""}{row.current?.value_json?.date ? ` (${row.current.value_json.date})` : ""}</option>)}<option value="new">Create a distinct meeting identity</option></select></label>
          {identities.data.total > 10 && <Pagination total={identities.data.total} page={identityPage} size={10} onPage={next => { setIdentityPage(next); setIdentityChoice(""); }} />}
        </>}
        {identityChoice === "new" && <><label className="block text-sm font-medium text-slate-700">Meeting name<input className={`${inputClass} mt-1`} maxLength={160} value={name} onChange={event => setName(event.target.value)} placeholder="e.g. UAT review" /></label><label className="block text-sm font-medium text-slate-700">Occurrence / release<input className={`${inputClass} mt-1`} maxLength={160} value={occurrence} onChange={event => setOccurrence(event.target.value)} placeholder="e.g. Release 1; leave empty for a one-off meeting" /></label><p className="text-xs text-slate-500">Give repeated meetings their own occurrence so different sessions do not overwrite each other.</p></>}
        <div className="flex flex-wrap gap-2"><button type="button" className={primaryClass} disabled={!projectId || !identityChoice || (identityChoice === "new" && !name.trim()) || stale} onClick={() => void resolve()}>Confirm identity &amp; compare</button>{!unverified && <button type="button" className={buttonClass} onClick={() => setMapping(null)}>Cancel matching</button>}</div>
      </fieldset>}
      {pending && !inactive && !mapping && !detail.active_resolution_id && <div className="flex flex-wrap gap-2"><button type="button" className={buttonClass} disabled={busy || dirty} onClick={() => void changeIdentity()}>Change meeting / project</button><button type="button" className={buttonClass} disabled={busy} onClick={() => { setEditing(!editing); setValue(proposal.value_json?.date ?? ""); setExcerpt(detail.evidence.excerpt ?? ""); }}>{editing ? "Cancel fact edit" : "Edit date / evidence"}</button></div>}
      {editing && <fieldset disabled={busy} className="space-y-3 rounded-xl border border-slate-200 p-3"><label className="block text-sm font-medium text-slate-700">Reviewed candidate date<input className={`${inputClass} mt-1`} type="date" value={value} onChange={event => setValue(event.target.value)} /></label><label className="block text-sm font-medium text-slate-700">Reviewed evidence passage<textarea className={`${inputClass} mt-1`} rows={4} maxLength={4000} value={excerpt} onChange={event => setExcerpt(event.target.value)} /></label><p className="text-xs text-slate-500">Use an exact source passage supporting the complete date. An edit needs a fresh comparison.</p><button type="button" className={buttonClass} disabled={!value || !excerpt.trim() || !dirty || stale} onClick={() => void saveEdit()}>Save fact edit</button></fieldset>}
      {pending && !inactive && !unverified && !mapping && !detail.active_resolution_id && <fieldset disabled={busy} className="space-y-3 border-t border-slate-100 pt-3">
        <button type="button" className={needsCompare ? primaryClass : buttonClass} disabled={dirty || stale} onClick={() => void compare()}>{needsCompare ? "Compare updated evidence" : "Compare again"}</button>
        {!needsCompare && detail.needs_resolution_reason && <div className="space-y-2 rounded-lg bg-amber-50 p-3"><p className="text-sm text-amber-900">{current ? "The sources do not establish an automatic replacement. Choosing the proposed date will supersede the current version; both evidence histories remain available." : "Pending sources disagree. Explain why this proposed date should become the first accepted value."}</p><label className="block text-sm font-medium text-slate-700">Reason for choosing the proposed date<textarea className={`${inputClass} mt-1`} rows={3} maxLength={2000} value={reason} onChange={event => setReason(event.target.value)} /></label></div>}
        {!needsCompare && !detail.can_approve && !detail.needs_resolution_reason && <p className="text-sm text-amber-800">Clarify the statement or correct the meeting match before approval. You can keep it unresolved.</p>}
        <div className="flex flex-wrap gap-2">{!needsCompare && (detail.can_approve || detail.needs_resolution_reason) && <button type="button" className={primaryClass} disabled={dirty || stale || (detail.needs_resolution_reason && !reason.trim())} onClick={() => void review("APPROVE")}>{approveLabel}</button>}<button type="button" className={buttonClass} disabled={dirty || stale} onClick={() => void review("REJECT")}>Reject proposal</button><button type="button" className={buttonClass} disabled={dirty || stale} onClick={() => void review("KEEP_UNRESOLVED")}>Keep unresolved</button></div>
      </fieldset>}
      {unverified && pending && !detail.active_resolution_id && <div className="flex flex-wrap gap-2"><button type="button" className={buttonClass} disabled={busy || stale} onClick={() => void review("REJECT")}>Reject candidate</button><button type="button" className={buttonClass} disabled={busy || stale} onClick={() => void review("KEEP_UNRESOLVED")}>Keep unresolved</button></div>}
    </>}
    {(stale || newerAvailable) && <div role="status" className="space-y-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-800"><p>The saved review may have changed. Reload before making another decision. Unsaved edits stay in this panel.</p><button type="button" className={buttonClass} disabled={busy} onClick={() => void reload()}>Reload latest review</button></div>}
    {error && <ErrorState message={error} variant="alert" />}{notice && <p role="status" className="text-sm text-emerald-700">{notice}</p>}
    {!unverified && <FactHistory identityId={detail.identity.id} />}
  </section>;
}

export function FactHistory({ identityId }: { identityId: string }) {
  const [open, setOpen] = useState(false);
  return <section className="border-t border-slate-100 pt-3"><button type="button" className="text-sm font-medium text-blue-700 hover:underline" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide version history" : "View version history"}</button>{open && <HistoryList identityId={identityId} />}</section>;
}
function HistoryList({ identityId }: { identityId: string }) {
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const path = `${FACT_PATH}/identities/${identityId}/history?limit=5&offset=${(page - 1) * 5}`;
  const query = useCachedQuery(path, () => factMemoryApi.history(path));
  return <div className="mt-3 space-y-3">{query.error ? <ErrorState message="Could not load version history." onRetry={query.retry} /> : !query.data ? <LoadingState label="Loading version history…" /> : <><ul className="space-y-2">{query.data.items.map(version => <li key={version.id}><button type="button" aria-pressed={selected === version.id} className="w-full rounded-lg border border-slate-200 p-3 text-left text-sm hover:bg-slate-50" onClick={() => setSelected(selected === version.id ? null : version.id)}><VersionLabel version={version} /></button>{selected === version.id && <div className="mt-2 rounded-lg bg-slate-50 p-3"><FactVersionPanel id={version.id} compact /></div>}</li>)}</ul>{query.data.total === 0 && <p className="text-xs text-slate-500">No approved versions yet.</p>}<Pagination total={query.data.total} page={page} size={5} onPage={next => { setPage(next); setSelected(null); }} /></>}</div>;
}
function VersionLabel({ version }: { version: FactVersion }) {
  return <><span className="block font-semibold text-slate-800">Version {version.version_number} · {version.value_json?.date ?? "Evidence removed"}</span><span className="mt-1 block text-xs text-slate-500">{version.status === "CURRENT" ? "Current" : "Superseded"} · {version.approved_by_name || "Workspace owner"} · {formatTimestamp(version.approved_at)}</span></>;
}
export function FactVersionPanel({ id, compact = false }: { id: string; compact?: boolean }) {
  const query = useCachedQuery(`${FACT_PATH}/versions/${id}`, () => factMemoryApi.version(id));
  if (query.error) return <ErrorState message="Could not load this fact version." onRetry={query.retry} />;
  if (!query.data) return <LoadingState label="Loading fact version…" />;
  const { version, identity } = query.data;
  return <section className="space-y-4" aria-label="Fact version details">
    {!compact && <div><h3 className="font-semibold text-slate-900">{identity.canonical_key}</h3><p className="mt-1 text-xs text-slate-500">{query.data.project_name}{identity.occurrence_key ? ` · ${identity.occurrence_key}` : ""}</p></div>}
    <VersionLabel version={version} />
    <p className="text-xs text-slate-500">{version.predecessor_id ? `Replaces version ${version.version_number - 1}. Earlier evidence remains in history.` : "First approved version."}</p>
    <VersionEvidenceList key={`evidence:${id}`} id={id} />
    <VersionReviewList key={`reviews:${id}`} id={id} />
    {!compact && <FactHistory identityId={identity.id} />}
  </section>;
}

function VersionEvidenceList({ id }: { id: string }) {
  const [page, setPage] = useState(1);
  const path = `${FACT_PATH}/versions/${id}/evidence?limit=5&offset=${(page - 1) * 5}`;
  const query = useCachedQuery(path, () => factMemoryApi.versionEvidence(path));
  return <section aria-label="Version evidence" className="space-y-2"><h4 className="text-xs font-semibold text-slate-600">Evidence for this version</h4>
    {query.error ? <ErrorState message="Could not load version evidence." onRetry={query.retry} /> : !query.data ? <LoadingState label="Loading evidence…" /> : <>{query.data.items.map(row => <FactEvidenceCard key={row.id} evidence={row} />)}{!query.data.items.length && <p className="text-xs text-slate-500">No retained evidence is available.</p>}<Pagination total={query.data.total} page={page} size={5} onPage={setPage} /></>}
  </section>;
}

function VersionReviewList({ id }: { id: string }) {
  const [page, setPage] = useState(1);
  const path = `${FACT_PATH}/versions/${id}/reviews?limit=5&offset=${(page - 1) * 5}`;
  const query = useCachedQuery(path, () => factMemoryApi.versionReviews(path));
  return <section aria-label="Version reviews" className="space-y-2"><h4 className="text-xs font-semibold text-slate-600">Review history</h4>
    {query.error ? <ErrorState message="Could not load version reviews." onRetry={query.retry} /> : !query.data ? <LoadingState label="Loading reviews…" /> : <>{query.data.items.map(row => <div key={row.id} className="rounded-lg border border-slate-200 p-3 text-xs text-slate-600"><p>{row.decision.replace(/_/g, " ").toLowerCase()} · {row.reviewer_name || "Workspace owner"} · {formatTimestamp(row.created_at)}</p>{!row.redacted_at && typeof row.detail.resolution_reason === "string" && <p className="mt-1 whitespace-pre-wrap">{row.detail.resolution_reason}</p>}{row.redacted_at && <p>Review content removed.</p>}</div>)}{!query.data.items.length && <p className="text-xs text-slate-500">No retained review details.</p>}<Pagination total={query.data.total} page={page} size={5} onPage={setPage} /></>}
  </section>;
}
