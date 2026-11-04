import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProposalDetail, { SourceAddButtons } from "@/components/workspace/SourceProposal";
import { clearApiCache } from "@/api/cache";
import type { SourceProposal } from "@/api/workspaceBrowser";

const sourceId = "11111111-1111-4111-8111-111111111111";
const projectId = "22222222-2222-4222-8222-222222222222";
const draftId = "33333333-3333-4333-8333-333333333333";
const factId = "44444444-4444-4444-8444-444444444444";
const createdProjectId = "66666666-6666-4666-8666-666666666666";
let draft: SourceProposal;
let calls: { path: string; method: string; body: Record<string, unknown> }[];
let enabled: boolean;
let sensitive: boolean;
let staleFact: boolean;
let generationFailure: "known" | "network" | "stale" | null;
let availableProjects: { id: string; name: string; entity_type: string }[];
let projectCreationWait: Promise<void> | null;
let projectCreationFails: boolean;

beforeEach(() => {
  clearApiCache(); calls = []; enabled = true; sensitive = false; staleFact = false; generationFailure = null;
  availableProjects = [{ id: projectId, name: "Project A", entity_type: "PROJECT" }, { id: "contact-id", name: "Contact B", entity_type: "CLIENT" }];
  projectCreationWait = null; projectCreationFails = false;
  draft = {
    id: draftId, source_kind: "email", source_id: sourceId, source_title: "UAT review",
    target: "knowledge", status: "SUGGESTED", payload: { summary: "UAT was rescheduled.", key_points: [] },
    evidence_text: "UAT was rescheduled to 18 September 2026.", analysis_truncated: true,
    provider: "openai", version: 1, result_id: null,
    fact_extraction: {
      id: "extraction-id", status: "SUCCEEDED", project_id: projectId, project_name: "Project A", generation: 1,
      coverage: { total_characters: 30000, analyzed_characters: 24000, ranges: [{ start: 0, end: 24000 }], partial: true },
      warnings: [], candidates: [{ fact_type: "MEETING_TIME", subject: "UAT review", occurrence: "release-1",
        value_date: "2026-09-18", original_value: "18 September 2026", modality: "ASSERTION",
        previous_value_date: "2026-09-17", change_cue: "rescheduled", evidence_text: "UAT was rescheduled to 18 September 2026.",
        source_context: "DIRECT", outcome: "PROPOSED", reason_codes: [], proposal_id: factId,
        proposal_revision: 1, proposal_status: "PENDING" }],
    },
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input).replace(/^\/api/, "");
    const method = options?.method ?? "GET";
    const body = JSON.parse(String(options?.body ?? "{}"));
    calls.push({ path, method, body });
    let data: unknown = draft; let status = 200;
    if (path.endsWith("/capabilities")) data = { enabled, supported_fact_types: ["MEETING_TIME"], max_characters: 24000, max_segments: 3 };
    else if (path === "/business-entities" && method === "GET") data = availableProjects;
    else if (path === "/business-entities" && method === "POST") {
      if (projectCreationWait) await projectCreationWait;
      if (projectCreationFails) { status = 503; data = { detail: "The project could not be saved. Please retry." }; }
      else { data = { id: createdProjectId, name: body.name, entity_type: "PROJECT" }; }
    }
    else if (path.endsWith("/proposals") && method === "POST") {
      if (generationFailure === "network") { generationFailure = null; throw new TypeError("Simulated response loss"); }
      if (generationFailure === "known") {
        generationFailure = null; status = 503; data = { detail: { code: "FACT_EXTRACTION_FAILED", message: "AI extraction failed. Please try again.", extraction_id: "failed-extraction-receipt", retryable: true } };
      }
      else if (generationFailure === "stale") { generationFailure = null; status = 409; data = { detail: "The source changed since this request. Start a new extraction." }; }
      else if (sensitive && !body.acknowledge_sensitive) { status = 409; data = { detail: { code: "SENSITIVE_ACK_REQUIRED" } }; }
      else if (!body.extract_facts) { draft = { ...draft, fact_extraction: undefined }; data = draft; }
    } else if (path === `/dev/fact-memory/proposals/${factId}` && method === "PATCH") {
      if (staleFact) { status = 409; data = { detail: "This proposal was edited. Reload before reviewing." }; }
      else {
        const candidate = draft.fact_extraction!.candidates[0];
        draft = { ...draft, fact_extraction: { ...draft.fact_extraction!, candidates: [{ ...candidate,
          value_date: body.value_date, evidence_text: body.excerpt, proposal_revision: candidate.proposal_revision! + 1 }] } };
        data = { id: "edit-review", decision: "EDIT" };
      }
    } else if (path.endsWith("/approve")) {
      draft = { ...draft, status: "APPROVED", payload: body.payload, version: draft.version + 1,
        result_id: "55555555-5555-4555-8555-555555555555" }; data = draft;
    }
    return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); clearApiCache(); });

function App() {
  return <MemoryRouter initialEntries={["/source"]}><Routes>
    <Route path="/source" element={<SourceAddButtons kind="email" id={sourceId} />} />
    <Route path="/workspace/:section" element={<ProposalDetail id={draftId} onClosed={() => {}} />} />
  </Routes></MemoryRouter>;
}

describe("selected-source fact extraction", () => {
  it("shows the resolved approval without offering edits to the original candidate", async () => {
    Object.assign(draft.fact_extraction!.candidates[0], {
      active_resolution_id: "resolved-id", resolved_status: "APPROVED", value_date: "2026-09-19",
      evidence_text: "UAT review is on 19 September 2026.",
    });
    render(<MemoryRouter><ProposalDetail id={draftId} onClosed={() => {}} /></MemoryRouter>);
    expect(await screen.findByText("Approved fact.")).toBeInTheDocument();
    expect(screen.getByText("2026-09-19")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View fact review" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit candidate" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve summary" })).toBeEnabled();
    expect(calls.some(call => call.method !== "GET")).toBe(false);
  });

  it("requires an explicit project and leaves non-project entities out of the choice", async () => {
    const user = userEvent.setup(); render(<App />);
    expect(calls).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    const dialog = await screen.findByRole("dialog", { name: "Choose knowledge destination" });
    expect(within(dialog).getByRole("button", { name: "Prepare summary & facts" })).toBeDisabled();
    expect(screen.queryByRole("option", { name: "Contact B" })).not.toBeInTheDocument();
    expect(calls.filter(call => call.method === "POST")).toHaveLength(0);
    await user.selectOptions(screen.getByLabelText("Project"), projectId);
    await user.click(screen.getByRole("button", { name: "Prepare summary & facts" }));
    await screen.findByLabelText("Summary");
    const request = calls.find(call => call.method === "POST")!;
    expect(request.body).toMatchObject({ target: "knowledge", extract_facts: true, project_id: projectId, acknowledge_sensitive: false });
    expect(request.body.request_key).toMatch(/^[0-9a-f-]{36}$/);
    expect(screen.getByText(/Analysed 24,000 of 30,000/)).toBeInTheDocument();
    expect(screen.getByText(/Included character ranges: 1–24000/)).toBeInTheDocument();
    expect(screen.getByText("Project: Project A")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve fact/ })).not.toBeInTheDocument();
  });

  it("keeps summary approval independent from every fact and keeps the pending cards visible", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><ProposalDetail id={draftId} onClosed={() => {}} /></MemoryRouter>);
    await screen.findByRole("button", { name: "Approve summary" });
    await user.click(screen.getByRole("button", { name: "Approve summary" }));
    expect(await screen.findByText("Summary approved. Fact candidates keep their separate review states.")).toBeInTheDocument();
    expect(screen.getByText("Unconfirmed · awaiting comparison and review.")).toBeInTheDocument();
    expect(draft.fact_extraction!.candidates[0].proposal_status).toBe("PENDING");
    expect(calls.filter(call => call.path.endsWith("/approve"))).toHaveLength(1);
    expect(calls.some(call => call.path.includes("/fact-memory/") && call.path.endsWith("/review"))).toBe(false);
  });

  it("edits only the chosen candidate and preserves unsaved summary edits during reload", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><ProposalDetail id={draftId} onClosed={() => {}} /></MemoryRouter>);
    const summary = await screen.findByLabelText("Summary");
    await user.clear(summary); await user.type(summary, "Keep my unsaved summary");
    await user.click(screen.getByRole("button", { name: "Edit candidate" }));
    fireEvent.change(screen.getByLabelText("Candidate date"), { target: { value: "2026-09-19" } });
    await user.click(screen.getByLabelText("Candidate date")); await user.keyboard("{Enter}");
    expect(calls.some(call => call.path.endsWith("/approve"))).toBe(false);
    fireEvent.change(screen.getByLabelText("Exact supporting passage"), { target: { value: "Alternative: 19 September 2026." } });
    await user.click(screen.getByRole("button", { name: "Save candidate" }));
    expect(await screen.findByText("Candidate saved. It remains unconfirmed.")).toBeInTheDocument();
    expect(summary).toHaveValue("Keep my unsaved summary");
    expect(screen.getByText("2026-09-19")).toBeInTheDocument();
    const edit = calls.find(call => call.path.includes("/fact-memory/") && call.method === "PATCH")!;
    expect(edit.body).toMatchObject({ expected_revision: 1, value_date: "2026-09-19", excerpt: "Alternative: 19 September 2026." });
    expect(calls.some(call => call.path.endsWith("/approve"))).toBe(false);
  });

  it("retains candidate edits after a stale-source or stale-revision refusal", async () => {
    staleFact = true;
    const user = userEvent.setup(); render(<MemoryRouter><ProposalDetail id={draftId} onClosed={() => {}} /></MemoryRouter>);
    await user.click(await screen.findByRole("button", { name: "Edit candidate" }));
    fireEvent.change(screen.getByLabelText("Candidate date"), { target: { value: "2026-09-19" } });
    await user.click(screen.getByRole("button", { name: "Save candidate" }));
    expect(await screen.findByText(/This proposal was edited/)).toBeInTheDocument();
    expect(screen.getByLabelText("Candidate date")).toHaveValue("2026-09-19");
    expect(draft.fact_extraction!.candidates[0].proposal_revision).toBe(1);
  });

  it("reuses the generation request key and project through sensitive-source acknowledgement", async () => {
    sensitive = true;
    const user = userEvent.setup(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await user.selectOptions(await screen.findByLabelText("Project"), projectId);
    await user.click(screen.getByRole("button", { name: "Prepare summary & facts" }));
    await user.click(await screen.findByRole("button", { name: "Confirm and prepare draft" }));
    await screen.findByLabelText("Summary");
    const posts = calls.filter(call => call.method === "POST");
    expect(posts).toHaveLength(2);
    expect(posts[0].body.request_key).toBe(posts[1].body.request_key);
    expect(posts[1].body).toMatchObject({ project_id: projectId, extract_facts: true, acknowledge_sensitive: true });
  });

  it("allows a summary without project facts and handles a valid zero-fact result", async () => {
    draft.fact_extraction!.candidates = [];
    const user = userEvent.setup(); render(<MemoryRouter><ProposalDetail id={draftId} onClosed={() => {}} /></MemoryRouter>);
    expect(await screen.findByText(/No supported meeting-date facts/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve summary" })).toBeEnabled();
    cleanup(); clearApiCache(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await user.click(await screen.findByRole("button", { name: "General summary only" }));
    await screen.findByLabelText("Summary");
    const post = calls.find(call => call.method === "POST")!;
    expect(post.body.extract_facts).toBeUndefined(); expect(post.body.project_id).toBeUndefined();
  });

  it("starts a fresh intent after a recorded failure and displays only its human message", async () => {
    generationFailure = "known";
    const user = userEvent.setup(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await user.selectOptions(await screen.findByLabelText("Project"), projectId);
    await user.click(screen.getByRole("button", { name: "Prepare summary & facts" }));
    expect(await screen.findByText("AI extraction failed. Please try again.")).toBeInTheDocument();
    expect(screen.queryByText("failed-extraction-receipt")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await user.click(await screen.findByRole("button", { name: "Prepare summary & facts" }));
    await screen.findByLabelText("Summary");
    const posts = calls.filter(call => call.method === "POST");
    expect(posts).toHaveLength(2);
    expect(posts[0].body.request_key).not.toBe(posts[1].body.request_key);
  });

  it("retains the intent key when a network failure leaves the outcome unknown", async () => {
    generationFailure = "network";
    const user = userEvent.setup(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await user.selectOptions(await screen.findByLabelText("Project"), projectId);
    await user.click(screen.getByRole("button", { name: "Prepare summary & facts" }));
    await screen.findByText("Something went wrong. Please retry.");
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await user.click(await screen.findByRole("button", { name: "Prepare summary & facts" }));
    await screen.findByLabelText("Summary");
    const posts = calls.filter(call => call.method === "POST");
    expect(posts).toHaveLength(2);
    expect(posts[0].body.request_key).toBe(posts[1].body.request_key);
  });

  it("starts a new intent after a definitive stale-source refusal", async () => {
    generationFailure = "stale";
    const user = userEvent.setup(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await user.selectOptions(await screen.findByLabelText("Project"), projectId);
    await user.click(screen.getByRole("button", { name: "Prepare summary & facts" }));
    await screen.findByText("The source changed since this request. Start a new extraction.");
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await user.click(await screen.findByRole("button", { name: "Prepare summary & facts" }));
    await screen.findByLabelText("Summary");
    const posts = calls.filter(call => call.path.endsWith("/proposals") && call.method === "POST");
    expect(posts[0].body.request_key).not.toBe(posts[1].body.request_key);
  });

  it("renders a redacted extraction without stale candidate text or missing-coverage errors", async () => {
    Object.assign(draft.fact_extraction!, { redacted_at: "2026-11-03T10:00:00Z", coverage: {} });
    render(<MemoryRouter><ProposalDetail id={draftId} onClosed={() => {}} /></MemoryRouter>);
    const section = await screen.findByRole("region", { name: "Extracted meeting-date candidates" });
    expect(within(section).getByText(/Evidence removed/)).toBeInTheDocument();
    expect(within(section).queryByText(/Proposed date/)).not.toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "Edit candidate" })).not.toBeInTheDocument();
    expect(within(section).queryByText(/Analysed/)).not.toBeInTheDocument();
  });

  it("creates an explicitly named project, selects it, and waits for a separate generation click", async () => {
    availableProjects = [];
    let finishCreation!: () => void;
    projectCreationWait = new Promise<void>(resolve => { finishCreation = resolve; });
    const user = userEvent.setup(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    const create = await screen.findByRole("button", { name: "Create project" });
    expect(create).toBeDisabled();
    await user.type(screen.getByLabelText("New project name"), "   ");
    expect(create).toBeDisabled();
    await user.clear(screen.getByLabelText("New project name"));
    await user.type(screen.getByLabelText("New project name"), "  New UAT Project  ");
    await user.click(create);
    expect(screen.getByRole("button", { name: "Creating project…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Prepare summary & facts" })).toBeDisabled();
    finishCreation();
    expect(await screen.findByText("Project created.")).toBeInTheDocument();
    expect(screen.getByLabelText("Project")).toHaveValue(createdProjectId);
    const createRequest = calls.find(call => call.path === "/business-entities" && call.method === "POST")!;
    expect(createRequest.body).toEqual({ name: "New UAT Project", entity_type: "PROJECT" });
    expect(calls.some(call => call.path.endsWith("/proposals") && call.method === "POST")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Prepare summary & facts" }));
    await screen.findByLabelText("Summary");
    const generation = calls.find(call => call.path.endsWith("/proposals") && call.method === "POST")!;
    expect(generation.body.project_id).toBe(createdProjectId);
    expect(generation.body.request_key).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("keeps the proposed project name after a failed explicit creation", async () => {
    projectCreationFails = true;
    const user = userEvent.setup(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await user.type(await screen.findByLabelText("New project name"), "Keep this project name");
    await user.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByText("The project could not be saved. Please retry.")).toBeInTheDocument();
    expect(screen.getByLabelText("New project name")).toHaveValue("Keep this project name");
    expect(calls.some(call => call.path.endsWith("/proposals") && call.method === "POST")).toBe(false);
  });
});
