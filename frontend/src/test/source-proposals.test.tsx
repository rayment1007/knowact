import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ProposalDetail, { OriginLinks, SourceAddButtons } from "@/components/workspace/SourceProposal";
import { mutationDependencies } from "@/api/cache";
import type { SourceProposal } from "@/api/workspaceBrowser";

let draft: SourceProposal;
let calls: { path: string; method: string; body: Record<string, unknown> }[];
let sensitive: boolean;
let approveFails: boolean;
const sourceId = "11111111-1111-4111-8111-111111111111";
const draftId = "22222222-2222-4222-8222-222222222222";
const resultId = "33333333-3333-4333-8333-333333333333";
beforeEach(() => {
  sensitive = false; approveFails = false; calls = [];
  draft = { id: draftId, source_kind: "calendar", source_id: sourceId, source_title: "Review meeting", target: "action", status: "SUGGESTED", payload: { title: "Prepare for review", description: "Review the source", due_date: null }, evidence_text: "Review the source", provider: "openai", version: 1, result_id: null, analysis_truncated: false };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input).replace(/^\/api/, "");
    const method = options?.method ?? "GET";
    const body = JSON.parse(String(options?.body ?? "{}"));
    calls.push({ path, method, body });
    let data: unknown = draft; let status = 200;
    if (path.endsWith("/proposals") && method === "POST") {
      if (sensitive && !body.acknowledge_sensitive) { status = 409; data = { detail: { code: "SENSITIVE_ACK_REQUIRED" } }; }
      else { draft = { ...draft, target: body.target, payload: body.target === "knowledge" ? { summary: "AI summary", key_points: ["First key point"] } : draft.payload }; data = draft; }
    } else if (path.endsWith("/approve")) {
      if (approveFails) { status = 409; data = { detail: "The original source changed. Discard this draft and generate a new one." }; }
      else { draft = { ...draft, payload: body.payload, status: "APPROVED", result_id: resultId, version: draft.version + 1 }; data = draft; }
    } else if (method === "PATCH") { draft = { ...draft, payload: body.payload, version: draft.version + 1 }; data = draft; }
    else if (path.endsWith("/reject")) { draft = { ...draft, status: "REJECTED" }; data = draft; }
    return new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function Destination() {
  const { search } = useLocation();
  return search.includes("proposal:") ? <ProposalDetail id={draftId} onClosed={() => {}} /> : <p>Created record</p>;
}
function App() {
  return <MemoryRouter initialEntries={["/source"]}><Routes>
    <Route path="/source" element={<SourceAddButtons kind="calendar" id={sourceId} />} />
    <Route path="/workspace/:section" element={<Destination />} />
  </Routes></MemoryRouter>;
}

describe("user-directed source drafting", () => {
  it("lets the user choose Action, edit fields, and approve exactly the displayed text", async () => {
    const user = userEvent.setup(); render(<App />);
    expect(calls).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Add to Actions" }));
    const title = await screen.findByLabelText("Title");
    expect(draft.status).toBe("SUGGESTED");
    expect(screen.getByLabelText("Due date")).toHaveValue("");
    await user.clear(title); await user.type(title, "User chosen action");
    await user.clear(screen.getByLabelText("Description")); await user.type(screen.getByLabelText("Description"), "My edited description");
    expect(calls.filter(c => c.path.endsWith("/approve"))).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Approve & create action" }));
    expect(await screen.findByText("Created record")).toBeInTheDocument();
    expect(calls.find(c => c.path.endsWith("/approve"))?.body).toEqual({ version: 1, payload: { title: "User chosen action", description: "My edited description", due_date: null } });
    expect(calls.some(c => /\/calendar\/.+\/add|\/gmail\/.+\/send/.test(c.path))).toBe(false);
  });

  it("saves a Knowledge draft without approving and uses its new version on approval", async () => {
    const user = userEvent.setup(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    const summary = await screen.findByLabelText("Summary");
    await user.clear(summary); await user.type(summary, "Edited summary");
    await user.clear(screen.getByLabelText("Key points")); await user.type(screen.getByLabelText("Key points"), "Point one\nPoint two");
    await user.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("Draft saved. It still needs your approval.")).toBeInTheDocument();
    expect(draft.status).toBe("SUGGESTED"); expect(draft.result_id).toBeNull();
    await user.click(screen.getByRole("button", { name: "Approve & create knowledge" }));
    await screen.findByText("Created record");
    expect(calls.find(c => c.path.endsWith("/approve"))?.body).toEqual({ version: 2, payload: { summary: "Edited summary", key_points: ["Point one", "Point two"] } });
  });

  it("retains edits on a failed approval and keeps the draft pending", async () => {
    approveFails = true;
    const user = userEvent.setup(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Actions" }));
    const title = await screen.findByLabelText("Title");
    await user.clear(title); await user.type(title, "Keep this edit");
    await user.click(screen.getByRole("button", { name: "Approve & create action" }));
    expect(await screen.findByText(/The original source changed/)).toBeInTheDocument();
    expect(title).toHaveValue("Keep this edit"); expect(draft.status).toBe("SUGGESTED");
    expect(screen.queryByText("Created record")).not.toBeInTheDocument();
  });

  it("waits for an explicit sensitive-source acknowledgement", async () => {
    sensitive = true;
    const user = userEvent.setup(); render(<App />);
    await user.click(screen.getByRole("button", { name: "Add to Knowledge" }));
    await screen.findByRole("button", { name: "Confirm and prepare draft" });
    expect(calls).toHaveLength(1); expect(calls[0].body.acknowledge_sensitive).toBe(false);
    await user.click(screen.getByRole("button", { name: "Confirm and prepare draft" }));
    await screen.findByLabelText("Summary");
    expect(calls.filter(c => c.method === "POST")[1].body.acknowledge_sensitive).toBe(true);
  });

  it("shows partial analysis and links only an available original", async () => {
    draft.analysis_truncated = true;
    render(<MemoryRouter><ProposalDetail id={draftId} onClosed={() => {}} /><OriginLinks origins={[{ kind: "file", id: sourceId, title: "Removed file", available: false, path: null }]} /></MemoryRouter>);
    expect(await screen.findByText(/Partial analysis/)).toBeInTheDocument();
    expect(screen.getByText(/Removed file.*original source no longer available/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Removed file/ })).not.toBeInTheDocument();
  });

  it("invalidates the related feeds, dashboard, provenance and drafts after mutations", () => {
    expect(mutationDependencies(`/workspace/proposals/${draftId}/approve`)).toEqual(expect.arrayContaining(["/workspace/items", "/workspace/counts", "/workspace/source", "/workspace/summary", "/workspace/actions", "/workspace/knowledge", "/workspace/proposals", "/knowledge", "/actions"]));
    for (const path of [`/documents/${sourceId}`, `/source-items/${sourceId}`]) {
      expect(mutationDependencies(path, "DELETE")).toEqual(expect.arrayContaining(["/workspace/actions", "/workspace/knowledge", "/workspace/proposals"]));
    }
  });
});
