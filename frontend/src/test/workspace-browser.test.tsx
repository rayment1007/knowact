import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import WorkspacePage, { LegacyWorkspaceRedirect } from "@/pages/WorkspacePage";

const sourceId = "11111111-1111-4111-8111-111111111111";
const nextId = "22222222-2222-4222-8222-222222222222";
const suggestionId = "33333333-3333-4333-8333-333333333333";
const source = { id: sourceId, source_type: "MANUAL", title: "Project meeting note", content: "Original meeting text", status: "NEW", created_at: "2026-09-30T08:00:00Z" };
let calls: { path: string; method: string }[];
let accepted: boolean;
let added: boolean;
const row = { id: sourceId, kind: "source", source_type: "note", title: source.title, excerpt: "Original preview", status: "RAW", updated_at: source.created_at };
const page = (items: unknown[], total = items.length) => ({ items, total, has_more: total > 20, type_counts: { note: total }, status_counts: { RAW: total } });

beforeEach(() => {
  calls = []; accepted = false; added = false;
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname.replace(/^\/api/, "");
    const method = init?.method ?? "GET";
    calls.push({ path: path + url.search, method });
    let data: unknown;
    if (path === "/business-entities") data = [];
    else if (path === "/workspace/counts") data = { sources: 21, knowledge: 0, actions: 1, sources_reviews: 0, knowledge_reviews: 0, actions_reviews: accepted ? 0 : 1 };
    else if (path === "/workspace/items") {
      if (url.searchParams.get("section") === "knowledge") data = page([]);
      else if (url.searchParams.get("section") === "actions") data = page(accepted ? [] : [{ ...row, id: suggestionId, kind: "suggestion", source_type: "action", title: "Review proposal", status: "SUGGESTED" }]);
      else if (url.searchParams.get("source_type") === "email") data = page([]);
      else if (url.searchParams.get("offset") === "20") data = page([{ ...row, id: nextId, title: "Next page note" }], 21);
      else data = page([row], added ? 22 : 21);
    } else if (path === `/workspace/source/source/${sourceId}`) data = { id: sourceId, kind: "source", title: source.title, source, document: null, raw_content: source.content, metadata: {} };
    else if (path.endsWith("/links")) data = { items: [], total: 0, has_more: false };
    else if (path === `/source-items/${sourceId}`) data = { source_item: source, classification: null };
    else if (path === "/source-items" && method === "POST") { added = true; data = source; }
    else if (path === `/workspace/suggestions/${suggestionId}`) data = { id: suggestionId, email_message_record_id: nextId, title: "Review proposal", description: "Check the proposal", evidence_text: "Please review", suggested_due_date: null, status: "SUGGESTED" };
    else if (path === `/gmail/suggestions/${suggestionId}/confirm` && method === "POST") { accepted = true; data = { status: "CONFIRMED" }; }
    else throw new Error(`Unexpected request: ${method} ${path}`);
    return new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
function Location() { const location = useLocation(); return <output aria-label="location">{location.pathname}{location.search}</output>; }
function renderWorkspace(path = "/workspace/sources") {
  return render(<MemoryRouter initialEntries={[path]}><Location /><Routes><Route path="/workspace/:section" element={<WorkspacePage />} /><Route path="/documents/:id" element={<LegacyWorkspaceRedirect />} /><Route path="/source-inbox" element={<LegacyWorkspaceRedirect />} /></Routes></MemoryRouter>);
}

describe("Workspace browser", () => {
  it("loads only the list until a record is opened, then closes its detail", async () => {
    const user = userEvent.setup(); renderWorkspace();
    await screen.findByRole("button", { name: source.title });
    expect(screen.queryByLabelText("Item details")).not.toBeInTheDocument();
    expect(calls.some(call => call.path.includes("/workspace/source/"))).toBe(false);
    await user.click(screen.getByRole("button", { name: source.title }));
    expect(await screen.findByRole("heading", { name: source.title })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Raw content" }));
    expect(screen.getByText(source.content)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "AI review" }));
    expect(await screen.findByRole("button", { name: "Classify" })).toBeInTheDocument();
    expect(calls.every(call => call.method === "GET")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Close details" }));
    expect(screen.queryByLabelText("Item details")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: source.title })).toHaveFocus();
  });

  it("requests the next server page on demand and reuses an unchanged cached page", async () => {
    const user = userEvent.setup(); renderWorkspace();
    await screen.findByRole("button", { name: source.title });
    expect(calls.filter(call => call.path.includes("offset=20"))).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByRole("button", { name: "Next page note" })).toBeInTheDocument();
    expect(calls.filter(call => call.path.includes("offset=20"))).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Previous" }));
    await screen.findByRole("button", { name: source.title });
    expect(calls.filter(call => call.path.startsWith("/workspace/items"))).toHaveLength(2);
    await user.click(screen.getByRole("link", { name: "Knowledge" }));
    await screen.findByText("No items match this view.");
    await user.click(screen.getByRole("link", { name: "Sources" }));
    await screen.findByRole("button", { name: source.title });
    expect(calls.filter(call => call.path.startsWith("/workspace/items"))).toHaveLength(3);
  });

  it("resets pagination and closes detail when a source filter changes", async () => {
    const user = userEvent.setup(); renderWorkspace(`/workspace/sources?page=2&item=source:${sourceId}`);
    await screen.findByRole("heading", { name: source.title });
    await user.click(screen.getByRole("button", { name: /Emails/ }));
    await screen.findByText("No items match this view.");
    expect(screen.getByLabelText("location")).toHaveTextContent("/workspace/sources?type=email");
    expect(screen.queryByLabelText("Item details")).not.toBeInTheDocument();
    expect(calls.some(call => call.path.includes("source_type=email") && call.path.includes("offset=0"))).toBe(true);
  });

  it("opens Add source as a dialog and saves a manual note only on submit", async () => {
    const user = userEvent.setup(); renderWorkspace(); await screen.findByRole("button", { name: source.title });
    expect(screen.queryByRole("textbox", { name: "Content" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add source" }));
    const dialog = screen.getByRole("dialog", { name: "Add source" });
    await user.type(within(dialog).getByLabelText("Title"), "A new note");
    await user.type(within(dialog).getByLabelText("Content"), "User-entered content");
    expect(calls.every(call => call.method === "GET")).toBe(true);
    await user.click(within(dialog).getByRole("button", { name: "Add note" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.filter(call => call.method === "POST" && call.path === "/source-items")).toHaveLength(1);
    await screen.findByRole("heading", { name: source.title });
    expect(screen.getByText("1–20 of 22")).toBeInTheDocument();
  });

  it("keeps suggestions pending until acceptance and updates the review badge", async () => {
    const user = userEvent.setup(); renderWorkspace("/workspace/actions");
    await user.click(await screen.findByRole("button", { name: "Review proposal" }));
    expect(await screen.findByRole("button", { name: "Accept into actions" })).toBeInTheDocument();
    expect(calls.every(call => call.method === "GET")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Accept into actions" }));
    await waitFor(() => expect(screen.queryByLabelText("Item details")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.queryByLabelText("1 pending reviews")).not.toBeInTheDocument());
    expect(calls.filter(call => call.method === "POST")).toEqual([{ path: `/gmail/suggestions/${suggestionId}/confirm`, method: "POST" }]);
  });

  it("preserves a legacy review link in the new sources tab", async () => {
    renderWorkspace("/source-inbox?review=1");
    await waitFor(() => expect(screen.getByLabelText("location")).toHaveTextContent("/workspace/sources?status=NEEDS_REVIEW"));
    expect(screen.getByRole("button", { name: /Review classification/ })).toHaveAttribute("aria-pressed", "true");
  });
});
