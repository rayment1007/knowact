import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import FactReview, { FactHistory, FactVersionPanel } from "@/components/workspace/FactReview";
import FactWorkspace from "@/components/workspace/FactWorkspace";
import { clearApiCache, invalidateApiCache } from "@/api/cache";
import { FACT_PATH, type FactProposalDetail, type FactVersion } from "@/api/factMemory";

const candidateId = "11111111-1111-4111-8111-111111111111";
const resolvedId = "22222222-2222-4222-8222-222222222222";
const identityId = "33333333-3333-4333-8333-333333333333";
const projectId = "44444444-4444-4444-8444-444444444444";
const headId = "55555555-5555-4555-8555-555555555555";
const stamp = "2026-11-04T01:00:00Z";
const fingerprint = "a".repeat(64);
let candidate: FactProposalDetail;
let resolved: FactProposalDetail;
let calls: { path: string; method: string; body: Record<string, unknown> }[];
let staleEdit: boolean;
let failReads: boolean;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
function version(id = headId, number = 1): FactVersion {
  return { id, identity_id: identityId, version_number: number, value_json: { date: "2026-09-17", precision: "DATE" },
    status: number === 1 ? "SUPERSEDED" : "CURRENT", predecessor_id: number === 1 ? null : headId,
    approved_by: "reviewer-id", approved_by_name: "Ray", approved_at: stamp, redacted_at: null, normalization_version: "M1_DATE_V1" };
}
function detail(): FactProposalDetail {
  return { proposal: { id: resolvedId, identity_id: identityId, evidence_id: "evidence-id", revision: 2,
    value_json: { date: "2026-09-18", precision: "DATE" }, relationship: "REVISION", comparison_ready: true,
    expected_head_id: headId, status: "PENDING", result_version_id: null, redacted_at: null, created_at: stamp, updated_at: stamp,
    extraction_context: { subject: "UAT review", identity_unverified: false },
    comparison_json: { reasons: ["The newer source explicitly reschedules this meeting."], can_approve: true, needs_resolution_reason: false } },
    identity: { id: identityId, project_id: projectId, fact_type: "MEETING_TIME", canonical_key: "uat review", occurrence_key: "release 1" },
    project_name: "Project A", current: { ...version(), status: "CURRENT" },
    evidence: { id: "evidence-id", excerpt: "UAT review moved from 17 September 2026 to 18 September 2026.", locator_json: {}, redacted_at: null,
      source: { id: "snapshot", source_kind: "email", source_id: candidateId, source_fingerprint: fingerprint, source_title: "Updated UAT email", source_time: stamp, captured_at: stamp, available: true, purged_at: null } },
    current_evidence: [], current_evidence_total: 0,
    lineage: { candidate_id: candidateId, candidate_revision: 1, resolved_proposal_id: resolvedId, active: true, candidate_current_revision: 1, candidate_status: "PENDING" },
    active_resolution_id: null, pending_peers: [], pending_peers_total: 0, can_approve: true, needs_resolution_reason: false };
}
beforeEach(() => {
  clearApiCache(); calls = []; staleEdit = false; failReads = false; resolved = detail();
  candidate = structuredClone(resolved);
  candidate.proposal = { ...candidate.proposal, id: candidateId, revision: 1, relationship: "UNCLEAR", comparison_ready: false,
    expected_head_id: null, extraction_context: { subject: "UAT review", identity_unverified: true }, comparison_json: {} };
  candidate.identity = { ...candidate.identity, canonical_key: "candidate:unverified" }; candidate.current = null;
  candidate.lineage = null; candidate.can_approve = false;
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = new URL(String(input), "http://localhost"), path = url.pathname.replace(/^\/api/, ""), method = options?.method ?? "GET";
    const body = JSON.parse(String(options?.body ?? "{}"));
    calls.push({ path: path + url.search, method, body });
    if (path === "/business-entities") return json([{ id: projectId, name: "Project A", entity_type: "PROJECT" }]);
    if (path === FACT_PATH + "/identities") return json({ items: [{ ...resolved.identity, project_name: "Project A", current: resolved.current }], total: 1, limit: 10, offset: 0 });
    if (path === FACT_PATH + "/counts") return json({ needs_review: 21, unresolved: 1, current: 1, history: 2 });
    if (path === FACT_PATH + "/memory") return json({ items: [{ id: resolvedId, record_type: "proposal", identity: resolved.identity,
      project_name: "Project A", proposal: resolved.proposal, version: null, title: url.searchParams.get("offset") === "20" ? "Next page UAT" : "First page UAT", value_date: "2026-09-18", relationship: "REVISION", status: "PENDING", created_at: stamp }], total: 21, limit: 20, offset: Number(url.searchParams.get("offset")) });
    if (path === `${FACT_PATH}/proposals/${candidateId}/resolve`) {
      candidate.active_resolution_id = resolvedId;
      return json(resolved);
    }
    if (path === `${FACT_PATH}/proposals/${resolvedId}/compare`) {
      resolved.proposal.expected_head_id = resolved.current?.id ?? null; resolved.proposal.revision++;
      resolved.proposal.comparison_ready = true; resolved.proposal.extraction_context.requires_revalidation = false;
      resolved.can_approve = true;
      return json(resolved);
    }
    if (path === `${FACT_PATH}/proposals/${resolvedId}/review`) {
      resolved.proposal.status = body.decision === "APPROVE" ? "APPROVED" : body.decision === "REJECT" ? "REJECTED" : "UNRESOLVED";
      resolved.can_approve = false;
      return json({ id: "review-id", decision: body.decision });
    }
    if (path === `${FACT_PATH}/proposals/${resolvedId}` && method === "PATCH") {
      if (staleEdit) return json({ detail: "This proposal was edited. Reload before reviewing." }, 409);
      resolved.proposal.revision++; resolved.proposal.value_json!.date = String(body.value_date); resolved.evidence.excerpt = String(body.excerpt);
      resolved.proposal.comparison_ready = false; resolved.can_approve = false;
      return json({ id: "edit-id", decision: "EDIT" });
    }
    if (path === `${FACT_PATH}/proposals/${resolvedId}` || path === `${FACT_PATH}/proposals/${candidateId}`) {
      return failReads ? json({ detail: "Unavailable" }, 503) : json(path.endsWith(candidateId) ? candidate : resolved);
    }
    if (path === `${FACT_PATH}/identities/${identityId}/history`) {
      const offset = Number(url.searchParams.get("offset"));
      return json({ items: [version(headId, offset ? 1 : 6)], total: 6, limit: 5, offset });
    }
    if (path === `${FACT_PATH}/versions/${headId}`) return json({ version: version(), identity: resolved.identity, project_name: "Project A", evidence: [resolved.evidence], evidence_total: 6, reviews: [], reviews_total: 6 });
    if (path === `${FACT_PATH}/versions/${headId}/evidence`) {
      const offset = Number(url.searchParams.get("offset"));
      return json({ items: [{ ...resolved.evidence, excerpt: offset ? "Earlier retained evidence" : "First retained evidence" }], total: 6, limit: 5, offset });
    }
    if (path === `${FACT_PATH}/versions/${headId}/reviews`) {
      const offset = Number(url.searchParams.get("offset"));
      return json({ items: [{ id: "review", decision: "APPROVE", reviewer_name: "Ray", created_at: stamp, detail: { resolution_reason: offset ? "Earlier review decision" : "First review decision" }, redacted_at: null }], total: 6, limit: 5, offset });
    }
    throw new Error(`Unexpected request: ${method} ${path}`);
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); clearApiCache(); });
function show(id = resolvedId) { return render(<MemoryRouter><FactReview id={id} /></MemoryRouter>); }

describe("independent fact identity and review", () => {
  it("requires an explicit meeting choice and binds resolution to the original revision and source", async () => {
    const user = userEvent.setup(); show(candidateId);
    const choose = await screen.findByLabelText("Meeting identity");
    expect(screen.getByRole("button", { name: "Confirm identity & compare" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    await user.selectOptions(choose, identityId);
    await user.click(screen.getByRole("button", { name: "Confirm identity & compare" }));
    await screen.findByRole("button", { name: "Approve replacement" });
    const request = calls.find(row => row.method === "POST")!;
    expect(request.path).toBe(`${FACT_PATH}/proposals/${candidateId}/resolve`);
    expect(request.body).toMatchObject({ expected_revision: 1, expected_source_fingerprint: fingerprint, project_id: projectId, identity_id: identityId, canonical_key: null });
    expect(calls.filter(row => row.path.endsWith("/review"))).toHaveLength(0);
  });

  it("requires a reason before accepting a conflicting date and records the displayed head", async () => {
    resolved.proposal.relationship = "CONFLICT"; resolved.needs_resolution_reason = true;
    const user = userEvent.setup(); show();
    const approve = await screen.findByRole("button", { name: "Approve chosen replacement" });
    expect(approve).toBeDisabled();
    await user.type(screen.getByLabelText("Reason for choosing the proposed date"), "Confirmed with the meeting owner.");
    await user.click(approve);
    await screen.findByText("Fact approved. Version history is preserved.");
    expect(calls.find(row => row.path.endsWith("/review"))?.body).toMatchObject({ decision: "APPROVE", expected_revision: 2, expected_head_id: headId, expected_source_fingerprint: fingerprint, resolution_reason: "Confirmed with the meeting owner." });
    expect(calls.some(row => /calendar|gmail|approve$/.test(row.path))).toBe(false);
  });

  it("preserves edited date and evidence after stale refusal and reload, then requires comparison", async () => {
    const user = userEvent.setup(); staleEdit = true; show();
    await user.click(await screen.findByRole("button", { name: "Edit date / evidence" }));
    fireEvent.change(screen.getByLabelText("Reviewed candidate date"), { target: { value: "2026-09-20" } });
    fireEvent.change(screen.getByLabelText("Reviewed evidence passage"), { target: { value: "UAT review is on 20 September 2026." } });
    await user.click(screen.getByRole("button", { name: "Save fact edit" }));
    await screen.findByText("This proposal was edited. Reload before reviewing.");
    resolved.proposal.revision = 3;
    await user.click(screen.getByRole("button", { name: "Reload latest review" }));
    await screen.findByText(/Your unsaved edits and reason were kept/);
    expect(screen.getByLabelText("Reviewed candidate date")).toHaveValue("2026-09-20");
    expect(screen.getByLabelText("Reviewed evidence passage")).toHaveValue("UAT review is on 20 September 2026.");
    expect(screen.getByRole("button", { name: "Change meeting / project" })).toBeDisabled();
    staleEdit = false;
    await user.click(screen.getByRole("button", { name: "Save fact edit" }));
    expect(await screen.findByRole("button", { name: "Compare updated evidence" })).toBeEnabled();
    expect(calls.filter(row => row.method === "PATCH").at(-1)?.body.expected_revision).toBe(3);
    expect(screen.queryByRole("button", { name: "Approve replacement" })).not.toBeInTheDocument();
  });

  it("offers a new comparison when the accepted head changed or peers changed", async () => {
    const user = userEvent.setup();
    resolved.current = version("66666666-6666-4666-8666-666666666666", 2); resolved.can_approve = false;
    show();
    await user.click(await screen.findByRole("button", { name: "Compare updated evidence" }));
    expect(await screen.findByRole("button", { name: "Approve replacement" })).toBeEnabled();
    expect(calls.find(row => row.path.endsWith("/compare"))?.body.expected_head_id).toBe(resolved.current.id);
    expect(screen.getByRole("button", { name: "Compare again" })).toBeEnabled();
  });

  it("does not discard open edits when an automatic read fails", async () => {
    const user = userEvent.setup(); show();
    await user.click(await screen.findByRole("button", { name: "Edit date / evidence" }));
    fireEvent.change(screen.getByLabelText("Reviewed evidence passage"), { target: { value: "Preserve this unsaved evidence." } });
    failReads = true;
    await act(async () => { invalidateApiCache([FACT_PATH]); });
    await screen.findByText(/Your open edits are preserved/);
    expect(screen.getByLabelText("Reviewed evidence passage")).toHaveValue("Preserve this unsaved evidence.");
  });

  it("shows a route back to the original when an identity match was invalidated", async () => {
    resolved.lineage!.active = false;
    const user = userEvent.setup(); show();
    expect(await screen.findByRole("button", { name: "Open original candidate" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open original candidate" }));
    await screen.findByLabelText("Meeting identity");
  });
});

describe("version history and saved fact browsing", () => {
  it("loads version history one page at a time", async () => {
    const user = userEvent.setup(); render(<MemoryRouter><FactHistory identityId={identityId} /></MemoryRouter>);
    expect(calls).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "View version history" }));
    await screen.findByText("Version 6 · 2026-09-17");
    expect(calls.some(row => row.path.includes("offset=5"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Version 1 · 2026-09-17")).toBeInTheDocument();
    expect(calls.some(row => row.path.includes("offset=5"))).toBe(true);
  });

  it("paginates evidence and reviewer decisions independently", async () => {
    const user = userEvent.setup(); render(<MemoryRouter><FactVersionPanel id={headId} compact /></MemoryRouter>);
    await screen.findByText("First retained evidence"); await screen.findByText("First review decision");
    expect(screen.getAllByText(/Ray/).length).toBeGreaterThan(0);
    const evidence = screen.getByRole("region", { name: "Version evidence" });
    const reviews = screen.getByRole("region", { name: "Version reviews" });
    await user.click(within(evidence).getByRole("button", { name: "Next" }));
    await screen.findByText("Earlier retained evidence");
    expect(screen.getByText("First review decision")).toBeInTheDocument();
    await user.click(within(reviews).getByRole("button", { name: "Next" }));
    await screen.findByText("Earlier review decision");
  });

  it("keeps list reads on demand and preserves an unchanged cached first page", async () => {
    const user = userEvent.setup(); render(<MemoryRouter initialEntries={["/workspace/knowledge?memory=needs_review"]}><FactWorkspace /></MemoryRouter>);
    await screen.findByText("First page UAT");
    expect(calls.some(row => row.path.includes("offset=20"))).toBe(false);
    await user.click(screen.getByRole("button", { name: "Next" }));
    await screen.findByText("Next page UAT");
    await user.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByText("First page UAT");
    expect(calls.filter(row => row.path.includes("/memory?") && row.path.includes("offset=0"))).toHaveLength(1);
    expect(calls.every(row => row.method === "GET")).toBe(true);
  });
});
