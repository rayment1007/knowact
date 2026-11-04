import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError, businessEntitiesApi, getErrorMessage, type BusinessEntity } from "@/api";
import { browserApi, itemLink, type FactGenerateOptions, type ProposalPayload, type SourceKind, type SourceOrigin, type SourceProposal } from "@/api/workspaceBrowser";
import { useCachedQuery } from "@/hooks/useCachedQuery";
import { ErrorState, LoadingState } from "@/components/feedback";
import { buttonClass, inputClass, primaryClass, StateBadge } from "./WorkspacePrimitives";
import FactCandidates from "./FactCandidates";

export function OriginLinks({ origins }: { origins: SourceOrigin[] }) {
  return <section className="space-y-2 text-sm"><h3 className="font-medium text-slate-700">Original source</h3>
    {!origins.length && <p className="text-xs text-slate-500">No original source is linked to this record.</p>}
    {origins.map(origin => origin.path ? <Link key={`${origin.kind}:${origin.id}`} className="block break-words text-blue-700 hover:underline" to={origin.path}>{origin.title} →</Link> : <p key={`${origin.kind}:${origin.id}`} className="text-xs text-slate-500">{origin.title} — original source no longer available.</p>)}
  </section>;
}

export function KnowledgeOrigin({ id }: { id: string }) {
  const query = useCachedQuery(`/workspace/knowledge/${id}/origin`, () => browserApi.knowledgeOrigin(id));
  if (query.error) return <ErrorState message="Could not load the original source link." onRetry={query.retry} />;
  return query.data ? <OriginLinks origins={query.data} /> : <LoadingState label="Loading original source…" />;
}

export function SourceAddButtons({ kind, id }: { kind: SourceKind; id: string }) {
  return <SourceAddControls key={`${kind}:${id}`} kind={kind} id={id} />;
}

function SourceAddControls({ kind, id }: { kind: SourceKind; id: string }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<"knowledge" | "action" | null>(null);
  const [sensitive, setSensitive] = useState<"knowledge" | "action" | null>(null);
  const [error, setError] = useState("");
  const [projectChoice, setProjectChoice] = useState(false);
  const [projects, setProjects] = useState<BusinessEntity[]>([]);
  const [projectId, setProjectId] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectCreated, setProjectCreated] = useState(false);
  const factRequest = useRef<FactGenerateOptions | undefined>();
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function generate(target: "knowledge" | "action", acknowledged = false, options = factRequest.current) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(target); setError(""); setSensitive(null);
    try {
      const proposal = await browserApi.generate(kind, id, target, acknowledged, target === "knowledge" ? options : undefined);
      if (mounted.current) navigate(itemLink({ kind: "proposal", id: proposal.id, source_type: proposal.target }));
    } catch (err) {
      if (!mounted.current) return;
      const detail = err instanceof ApiError ? (err.body as { detail?: unknown })?.detail : undefined;
      const detailObject = detail && typeof detail === "object" ? detail as { extraction_id?: unknown; code?: unknown } : undefined;
      // A persisted failure receipt is a known outcome. A new retry needs a
      // fresh intent key; network errors retain the key to replay a possible
      // success whose response did not reach this browser.
      if (err instanceof ApiError && ((err.status === 503 && typeof detailObject?.extraction_id === "string"
          && detailObject.extraction_id.trim()) || (err.status === 409 && detailObject?.code !== "SENSITIVE_ACK_REQUIRED"))) factRequest.current = undefined;
      if (err instanceof ApiError && (err.body as { detail?: { code?: string } })?.detail?.code === "SENSITIVE_ACK_REQUIRED") setSensitive(target);
      else setError(getErrorMessage(err));
    } finally { inFlight.current = false; if (mounted.current) setBusy(null); }
  }
  async function chooseKnowledge() {
    if (inFlight.current) return;
    inFlight.current = true; setBusy("knowledge"); setError(""); setSensitive(null);
    let generalOnly = false;
    try {
      const capabilities = await browserApi.factCapabilities();
      if (capabilities.enabled === true) {
        const entities = await businessEntitiesApi.list();
        if (mounted.current) { setProjects(entities.filter(entity => entity.entity_type === "PROJECT")); setProjectChoice(true); }
      } else generalOnly = true;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) generalOnly = true;
      else if (mounted.current) setError(getErrorMessage(err));
    } finally { inFlight.current = false; if (mounted.current) setBusy(null); }
    if (generalOnly && mounted.current) { factRequest.current = undefined; await generate("knowledge", false, undefined); }
  }
  function prepareFacts() {
    if (!projectId) return;
    if (factRequest.current?.project_id !== projectId) factRequest.current = { extract_facts: true, project_id: projectId, request_key: crypto.randomUUID() };
    setProjectChoice(false); void generate("knowledge", false, factRequest.current);
  }
  async function createProject() {
    const name = newProjectName.trim();
    if (!name || inFlight.current) return;
    inFlight.current = true; setCreatingProject(true); setError(""); setProjectCreated(false);
    try {
      const project = await businessEntitiesApi.createProject(name);
      if (mounted.current) {
        setProjects(previous => [...previous.filter(item => item.id !== project.id), project]);
        setProjectId(project.id); setNewProjectName(""); setProjectCreated(true);
      }
    } catch (err) { if (mounted.current) setError(getErrorMessage(err)); }
    finally { inFlight.current = false; if (mounted.current) setCreatingProject(false); }
  }
  return <section aria-label="Add source to workspace" className="space-y-3 rounded-xl border border-blue-100 bg-blue-50/40 p-4">
    <div className="flex flex-wrap gap-2">
      <button className={primaryClass} disabled={!!busy || projectChoice} onClick={() => void chooseKnowledge()}>{busy === "knowledge" ? "Preparing draft…" : "Add to Knowledge"}</button>
      <button className={buttonClass} disabled={!!busy || projectChoice} onClick={() => void generate("action")}>{busy === "action" ? "Preparing draft…" : "Add to Actions"}</button>
    </div>
    {projectChoice && <div role="dialog" aria-label="Choose knowledge destination" className="space-y-3 rounded-xl border border-blue-100 bg-white p-4">
      <h3 className="font-medium text-slate-900">Add knowledge</h3>
      <p className="text-sm text-slate-600">Save a general summary, or choose a project to also extract meeting-date facts for separate review.</p>
      <fieldset disabled={creatingProject} className="space-y-3">
        <label className="block text-sm font-medium text-slate-700">Project<select className={`${inputClass} mt-1`} value={projectId} onChange={e => setProjectId(e.target.value)}><option value="">Choose a project</option>{projects.map(project => <option value={project.id} key={project.id}>{project.name}</option>)}</select></label>
        {!projects.length && <p className="text-xs text-slate-500">Create a project below to extract project facts, or prepare a general summary.</p>}
        <div className="space-y-2 rounded-lg bg-slate-50 p-3"><label className="block text-sm font-medium text-slate-700">New project name<input maxLength={255} className={`${inputClass} mt-1`} value={newProjectName} onChange={event => { setNewProjectName(event.target.value); setProjectCreated(false); }} /></label><button className={buttonClass} disabled={!newProjectName.trim()} onClick={() => void createProject()}>{creatingProject ? "Creating project…" : "Create project"}</button></div>
        {projectCreated && <p role="status" className="text-xs text-emerald-700">Project created.</p>}
        <div className="flex flex-wrap gap-2"><button className={primaryClass} disabled={!projectId} onClick={prepareFacts}>Prepare summary &amp; facts</button><button className={buttonClass} onClick={() => { factRequest.current = undefined; setProjectChoice(false); void generate("knowledge", false, undefined); }}>General summary only</button><button className={buttonClass} onClick={() => setProjectChoice(false)}>Cancel</button></div>
      </fieldset>
    </div>}
    {busy && <p role="status" className="text-sm text-blue-700">Reading source and preparing your draft…</p>}
    {sensitive && <div role="alert" className="space-y-2 text-sm text-amber-800"><p>This source is marked highly sensitive. Send its text to the configured AI provider to prepare this draft?</p><button className={buttonClass} onClick={() => void generate(sensitive, true)}>Confirm and prepare draft</button><button className={`${buttonClass} ml-2`} onClick={() => setSensitive(null)}>Cancel</button></div>}
    {error && <ErrorState message={error} variant="alert" />}
  </section>;
}

function ProposalEditor({ initial, onClosed }: { initial: SourceProposal; onClosed: () => void }) {
  const navigate = useNavigate();
  const [proposal, setProposal] = useState(initial);
  const [payload, setPayload] = useState<ProposalPayload>(initial.payload);
  const [points, setPoints] = useState((initial.payload.key_points ?? []).join("\n"));
  const [busy, setBusy] = useState(false);
  const [factBusy, setFactBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const knowledge = proposal.target === "knowledge";
  async function reloadFacts() {
    const latest = await browserApi.proposal(proposal.id);
    setProposal(latest);
  }
  // Do not overwrite unsaved edits when background cache reads finish. Server
  // version checks reject stale approvals; reopening loads the latest draft.
  const dirty = JSON.stringify(payload) !== JSON.stringify(proposal.payload) || points !== (proposal.payload.key_points ?? []).join("\n");
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);
  async function run(operation: "save" | "approve" | "reject") {
    if (busy || factBusy) return;
    setBusy(true); setError(""); setSaved(false);
    const edited = knowledge ? { summary: payload.summary, key_points: points.split("\n").map(p => p.trim()).filter(Boolean) } : payload;
    try {
      if (operation === "reject") { await browserApi.rejectProposal(proposal.id, proposal.version); onClosed(); }
      else {
        const updated = operation === "approve" ? await browserApi.approveProposal(proposal.id, proposal.version, edited) : await browserApi.saveProposal(proposal.id, proposal.version, edited);
        if (operation === "approve" && updated.result_id && !updated.fact_extraction) navigate(itemLink({ kind: updated.target, id: updated.result_id }));
        else { setProposal(updated); setPayload(updated.payload); setPoints((updated.payload.key_points ?? []).join("\n")); setSaved(true); }
      }
    } catch (err) { setError(getErrorMessage(err)); } finally { setBusy(false); }
  }
  if (proposal.status !== "SUGGESTED") return <div className="space-y-4 p-5"><p>{proposal.fact_extraction && proposal.status === "APPROVED" ? "Summary approved. Fact candidates keep their separate review states." : `This draft has been ${proposal.status === "APPROVED" ? "approved" : "discarded"}.`}</p>{proposal.result_id && <Link className="text-blue-700" to={itemLink({ kind: proposal.target, id: proposal.result_id })}>Open created {proposal.target} →</Link>}{proposal.fact_extraction && <FactCandidates extraction={proposal.fact_extraction} disabled={factBusy} onReload={reloadFacts} onBusyChange={setFactBusy} />}</div>;
  return <form className="space-y-5 p-5" onSubmit={e => { e.preventDefault(); void run("approve"); }}>
    <div className="flex flex-wrap items-center gap-2"><StateBadge status="SUGGESTED" /><span className="text-xs text-slate-500">{knowledge ? "Knowledge" : "Action"} draft · Requested by you</span></div>
    <h2 className="text-lg font-semibold text-slate-900">Review your {proposal.target} draft</h2>
    <p className="text-xs leading-relaxed text-slate-500">Edit the AI draft below. Approve saves these exact edits and creates the {proposal.target}. Save draft keeps it pending. Save your edits before leaving this view.</p>
    <Link to={itemLink({ kind: proposal.source_kind, id: proposal.source_id })} className="block break-words text-sm text-blue-700 hover:underline">Original source: {proposal.source_title} →</Link>
    {proposal.provider === "mock" && <p role="status" className="rounded-lg bg-amber-50 p-3 text-xs text-amber-800">Demo provider: this is a sample draft, not a live AI analysis.</p>}
    {proposal.analysis_truncated && !proposal.fact_extraction && <p role="status" className="rounded-lg bg-amber-50 p-3 text-xs text-amber-800">Partial analysis: only the first 24,000 characters of the source were included. Review the original for omitted details.</p>}
    <fieldset disabled={busy || factBusy} className="space-y-4">
      {knowledge ? <>
        <label className="block text-sm font-medium text-slate-700">Summary<textarea required rows={5} maxLength={12000} className={`${inputClass} mt-1`} value={payload.summary ?? ""} onChange={e => { setPayload({ ...payload, summary: e.target.value }); setSaved(false); }} /></label>
        <label className="block text-sm font-medium text-slate-700">Key points <span className="font-normal text-slate-400">(one per line)</span><textarea aria-label="Key points" rows={5} className={`${inputClass} mt-1`} value={points} onChange={e => { setPoints(e.target.value); setSaved(false); }} /></label>
      </> : <>
        <label className="block text-sm font-medium text-slate-700">Title<input required maxLength={512} className={`${inputClass} mt-1`} value={payload.title ?? ""} onChange={e => { setPayload({ ...payload, title: e.target.value }); setSaved(false); }} /></label>
        <label className="block text-sm font-medium text-slate-700">Description<textarea rows={6} maxLength={12000} className={`${inputClass} mt-1`} value={payload.description ?? ""} onChange={e => { setPayload({ ...payload, description: e.target.value }); setSaved(false); }} /></label>
        <label className="block text-sm font-medium text-slate-700">Due date <span className="font-normal text-slate-400">(optional)</span><input aria-label="Due date" type="date" className={`${inputClass} mt-1`} value={payload.due_date ?? ""} onChange={e => { setPayload({ ...payload, due_date: e.target.value || null }); setSaved(false); }} /></label>
      </>}
    </fieldset>
    <section className="rounded-xl bg-slate-50 p-4"><h3 className="mb-2 text-xs font-semibold text-slate-600">Supporting passage from the source</h3><blockquote className="whitespace-pre-wrap break-words text-sm leading-relaxed text-slate-600">{proposal.evidence_text}</blockquote></section>
    {proposal.fact_extraction && <FactCandidates extraction={proposal.fact_extraction} disabled={busy || factBusy} onReload={reloadFacts} onBusyChange={setFactBusy} />}
    {error && <ErrorState message={error} variant="alert" />}
    {saved && <p role="status" className="text-sm text-emerald-700">Draft saved. It still needs your approval.</p>}
    <div className="flex flex-wrap gap-2"><button className={primaryClass} disabled={busy || factBusy || !(knowledge ? payload.summary?.trim() : payload.title?.trim())}>{busy ? "Saving…" : proposal.fact_extraction ? "Approve summary" : `Approve & create ${knowledge ? "knowledge" : "action"}`}</button><button type="button" className={buttonClass} disabled={busy || factBusy} onClick={() => void run("save")}>Save draft</button><button type="button" className={buttonClass} disabled={busy || factBusy} onClick={() => void run("reject")}>{proposal.fact_extraction ? "Discard summary draft" : "Discard draft"}</button></div>
    {!knowledge && <p className="text-xs text-slate-500">Creating this action does not send email or change Google Calendar.</p>}
  </form>;
}

export default function ProposalDetail({ id, onClosed }: { id: string; onClosed: () => void }) {
  const query = useCachedQuery(`/workspace/proposals/${id}`, () => browserApi.proposal(id));
  if (query.error) return <ErrorState className="m-5" message="This draft could not be loaded. The source or draft may have been removed." onRetry={query.retry} />;
  return query.data ? <ProposalEditor key={id} initial={query.data} onClosed={onClosed} /> : <LoadingState className="p-5" label="Loading draft…" />;
}
