import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Link, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { gmailApi, integrationsApi, type IntegrationConnection } from "@/api";
import { workspaceApi } from "@/api/workspace";
import { WorkspaceProvider } from "@/components/WorkspaceProvider";
import SyncStatusBar from "@/components/SyncStatusBar";
import WorkspaceSearch from "@/components/WorkspaceSearch";
import FloatingAssistant from "@/components/FloatingAssistant";

vi.mock("@/api", () => ({
  gmailApi: { syncNow: vi.fn() },
  integrationsApi: { list: vi.fn() },
}));
vi.mock("@/api/workspace", () => ({ workspaceApi: {
  summary: vi.fn(), syncCalendar: vi.fn(), search: vi.fn(),
} }));
vi.mock("@/pages/CopilotPage", () => ({ default: () => <textarea aria-label="Assistant message" /> }));

function connection(id: string, service: IntegrationConnection["service"], status: IntegrationConnection["status"] = "CONNECTED"): IntegrationConnection {
  return { id, service, status, last_sync_at: "2026-09-27T00:00:00Z", last_error: null } as IntegrationConnection;
}
function Path() { return <output aria-label="path">{useLocation().pathname}</output>; }
beforeEach(() => {
  vi.mocked(integrationsApi.list).mockResolvedValue([]);
  vi.mocked(workspaceApi.summary).mockResolvedValue({ documents: 2, documents_pending: 0, documents_failed: 0, notes: 1, knowledge: 2, open_actions: 3, source_reviews: 0, knowledge_reviews: 0 });
  vi.mocked(gmailApi.syncNow).mockResolvedValue({} as never);
  vi.mocked(workspaceApi.syncCalendar).mockResolvedValue({ events_synced: 2 });
});
afterEach(() => { cleanup(); vi.resetAllMocks(); });

describe("workspace login sync", () => {
  it("syncs each connected service once in StrictMode and not again on navigation", async () => {
    vi.mocked(integrationsApi.list).mockResolvedValue([
      connection("mail", "GMAIL"), connection("calendar", "GOOGLE_CALENDAR"), connection("revoked", "GMAIL", "REVOKED"),
    ]);
    const user = userEvent.setup();
    render(<StrictMode><MemoryRouter><WorkspaceProvider><SyncStatusBar /><Link to="/actions">Go to actions</Link></WorkspaceProvider></MemoryRouter></StrictMode>);
    await user.click(screen.getByRole("button", { name: "Sync status" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled());
    expect(gmailApi.syncNow).toHaveBeenCalledExactlyOnceWith("mail");
    expect(workspaceApi.syncCalendar).toHaveBeenCalledExactlyOnceWith("calendar");
    const connectionReads = vi.mocked(integrationsApi.list).mock.calls.length;
    const summaryReads = vi.mocked(workspaceApi.summary).mock.calls.length;
    await user.click(screen.getByRole("link", { name: "Go to actions" }));
    expect(gmailApi.syncNow).toHaveBeenCalledTimes(1);
    expect(integrationsApi.list).toHaveBeenCalledTimes(connectionReads);
    expect(workspaceApi.summary).toHaveBeenCalledTimes(summaryReads);
    await user.click(screen.getByRole("button", { name: "Sync status" }));
    await user.click(screen.getByRole("button", { name: "Sync Now" }));
    await waitFor(() => expect(gmailApi.syncNow).toHaveBeenCalledTimes(2));
  });

  it("reports a failed Gmail read while still completing Calendar and permits retry", async () => {
    vi.mocked(integrationsApi.list).mockResolvedValue([connection("mail", "GMAIL"), connection("calendar", "GOOGLE_CALENDAR")]);
    vi.mocked(gmailApi.syncNow).mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup();
    render(<MemoryRouter><WorkspaceProvider><SyncStatusBar /></WorkspaceProvider></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "Sync status" }));
    expect(await screen.findByText(/Sync failed/)).toBeInTheDocument();
    expect(screen.getByText(/Last successful sync:.*2026, \d{2}:\d{2}:\d{2}/)).toBeInTheDocument();
    expect(workspaceApi.syncCalendar).toHaveBeenCalledOnce();
    await user.click(screen.getByRole("button", { name: "Sync Now" }));
    await waitFor(() => expect(screen.queryByText(/Sync failed/)).not.toBeInTheDocument());
  });
});

describe("unified workspace search", () => {
  it("ignores a stale result and opens the selected raw source", async () => {
    let resolveOld!: (value: never) => void;
    vi.mocked(workspaceApi.search).mockImplementation(query => query === "old" ? new Promise(resolve => { resolveOld = resolve; }) : Promise.resolve({ total: 1, has_more: false, items: [{ id: "source-1", title: "Current source", kind: "note", status: "NEW", excerpt: "Original content", updated_at: "2026-09-27", path: "/source-inbox/source-1" }] }));
    const user = userEvent.setup();
    render(<MemoryRouter><WorkspaceSearch /><Path /></MemoryRouter>);
    const input = screen.getByRole("searchbox");
    await user.type(input, "old");
    await waitFor(() => expect(workspaceApi.search).toHaveBeenCalled());
    await user.clear(input); await user.type(input, "new");
    expect(await screen.findByText("Current source")).toBeInTheDocument();
    await act(async () => resolveOld({ total: 0, has_more: false, items: [] } as never));
    expect(screen.getByText("Current source")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: /Current source/ }));
    expect(screen.getByLabelText("path")).toHaveTextContent("/source-inbox/source-1");
  });
});

describe("floating Copilot", () => {
  it("preserves the conversation input when closed and across page navigation", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><FloatingAssistant /><Link to="/actions">Actions</Link></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "Open assistant" }));
    await user.type(screen.getByLabelText("Assistant message"), "Review my project");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "Actions" }));
    await user.click(screen.getByRole("button", { name: "Open assistant" }));
    expect(screen.getByLabelText("Assistant message")).toHaveValue("Review my project");
  });
});
