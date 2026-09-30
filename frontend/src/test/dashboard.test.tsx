import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import EnterpriseDashboardPage from "@/pages/EnterpriseDashboardPage";
import { api } from "@/api/client";

const workspace = vi.hoisted(() => ({
  summary: { documents: 2, notes: 1, open_actions: 1, knowledge: 3, source_reviews: 2, knowledge_reviews: 1 },
  connections: [{ id: "mail", service: "GMAIL", status: "CONNECTED", last_error: null }],
  loading: false, syncing: false, error: null as string | null, failures: {} as Record<string, string>,
}));
vi.mock("@/auth", () => ({ useAuth: () => ({ user: { full_name: "Ray Lee" } }) }));
vi.mock("@/components/WorkspaceProvider", () => ({ useWorkspace: () => workspace }));

let actions: { id: string; title: string; due_date: string | null; status: string; created_at: string }[];
let calls: string[];
let failActions = false;
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(2026, 8, 28, 13, 21, 34));
  calls = []; failActions = false;
  workspace.error = null; workspace.failures = {}; workspace.summary.source_reviews = 2; workspace.summary.knowledge_reviews = 1;
  actions = [{ id: "one", title: "Finish checklist", due_date: "2026-09-28", status: "OPEN", created_at: "2026-09-01T00:00:00Z" }];
  vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
    calls.push(`${options.method} ${url}`);
    if (options.method === "PATCH") { actions = actions.map(action => ({ ...action, status: "DONE" })); return json(actions[0]); }
    if (url === "/api/actions") return json(actions, failActions ? 503 : 200);
    if (url === "/api/workspace/calendar") return json([]);
    if (url.includes("/workspace/activity")) return json({ items: [], has_more: false });
    if (url.includes("/workspace/search")) return json({ items: [], total: 0, has_more: false });
    throw new Error(`Unexpected request ${url}`);
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("dashboard brief and review entry points", () => {
  it("shows the local date, true due-work summary and exact review filters", async () => {
    render(<MemoryRouter><EnterpriseDashboardPage /></MemoryRouter>);
    expect(await screen.findByText("1 action due today.")).toBeInTheDocument();
    expect(screen.getByText(/Mon, 28 Sept? 2026/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Sources to review/ })).toHaveAttribute("href", "/source-inbox?review=1");
    expect(screen.getByRole("link", { name: /Knowledge to review/ })).toHaveAttribute("href", "/knowledge?status=SUGGESTED");
    expect(screen.queryByText("High")).not.toBeInTheDocument();
    expect(screen.queryByText(/Conflict/)).not.toBeInTheDocument();
    expect(calls.some(call => call.includes("/brief/daily"))).toBe(false);
  });

  it("keeps the dashboard ready on return and updates a completed action immediately", async () => {
    const first = render(<MemoryRouter><EnterpriseDashboardPage /></MemoryRouter>);
    await screen.findByText("1 action due today.");
    first.unmount();
    const reads = calls.length;
    render(<MemoryRouter><EnterpriseDashboardPage /></MemoryRouter>);
    expect(screen.getByText("1 action due today.")).toBeInTheDocument();
    await act(async () => {});
    expect(calls).toHaveLength(reads);
    await act(async () => { await api.patch("/actions/one", { status: "DONE" }); });
    await waitFor(() => expect(screen.queryByText("1 action due today.")).not.toBeInTheDocument());
    expect(screen.getByText("3 suggestions ready for your review.")).toBeInTheDocument();
    expect(screen.queryByText("Finish checklist")).not.toBeInTheDocument();
    expect(calls.filter(call => call.endsWith("/workspace/calendar"))).toHaveLength(1);
  });

  it("uses a light empty state only when the loaded workspace is quiet", async () => {
    actions = []; workspace.summary.source_reviews = 0; workspace.summary.knowledge_reviews = 0;
    render(<MemoryRouter><EnterpriseDashboardPage /></MemoryRouter>);
    expect(await screen.findByText("Your to-do list is taking a well-earned coffee break.")).toBeInTheDocument();
  });

  it("does not claim everything is clear if loading or external sync failed", async () => {
    actions = []; workspace.summary.source_reviews = 0; workspace.summary.knowledge_reviews = 0;
    workspace.failures = { mail: "Failed" };
    const first = render(<MemoryRouter><EnterpriseDashboardPage /></MemoryRouter>);
    await screen.findByText("Your saved workspace is quiet for now.");
    expect(screen.getByText(/Some sources could not sync/)).toBeInTheDocument();
    expect(screen.queryByText(/coffee break/)).not.toBeInTheDocument();
    first.unmount();
    workspace.error = "Could not refresh";
    render(<MemoryRouter><EnterpriseDashboardPage /></MemoryRouter>);
    expect(await screen.findByText(/This brief uses saved data and may be incomplete/)).toBeInTheDocument();
    expect(screen.getByText("Your saved workspace is quiet for now.")).toBeInTheDocument();
    expect(screen.queryByText(/coffee break/)).not.toBeInTheDocument();
  });

  it("keeps loaded tasks visible when connection status could not refresh", async () => {
    workspace.error = "Could not read Google connections for this sync";
    render(<MemoryRouter><EnterpriseDashboardPage /></MemoryRouter>);
    expect(await screen.findByText("1 action due today.")).toBeInTheDocument();
    expect(screen.getByText(/This brief uses saved data and may be incomplete/)).toBeInTheDocument();
    expect(screen.queryByText(/Your brief is temporarily unavailable/)).not.toBeInTheDocument();
  });

  it("does not invent a brief when tasks have never loaded successfully", async () => {
    failActions = true;
    render(<MemoryRouter><EnterpriseDashboardPage /></MemoryRouter>);
    expect(await screen.findByText(/Your brief is temporarily unavailable/)).toBeInTheDocument();
    expect(screen.queryByText("1 action due today.")).not.toBeInTheDocument();
    expect(screen.queryByText(/coffee break/)).not.toBeInTheDocument();
  });
});
