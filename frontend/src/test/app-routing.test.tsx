import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Outlet } from "react-router-dom";

vi.mock("@/auth", () => ({
  ProtectedRoute: () => <Outlet />,
}));

vi.mock("@/layouts/AppShell", () => ({
  default: () => (
    <div>
      <span data-testid="app-shell">Workspace shell</span>
      <Outlet />
    </div>
  ),
}));

vi.mock("@/pages/LoginPage", () => ({ default: () => <h1>Login page</h1> }));
vi.mock("@/pages/EnterpriseDashboardPage", () => ({
  default: () => <h1>Dashboard page</h1>,
}));
vi.mock("@/pages/SourceInboxPage", () => ({
  default: () => <h1>Source Inbox page</h1>,
}));
vi.mock("@/pages/KnowledgeHubPage", () => ({
  default: () => <h1>Knowledge Hub page</h1>,
}));
vi.mock("@/pages/ActionCenterPage", () => ({
  default: () => <h1>Action Center page</h1>,
}));
vi.mock("@/pages/DecisionMemoryPage", () => ({
  default: () => <h1>Decision Memory page</h1>,
}));
vi.mock("@/pages/IntegrationsPage", () => ({
  default: () => <h1>Integrations page</h1>,
}));
vi.mock("@/pages/GmailSyncPage", () => ({
  default: () => <h1>Gmail Sync page</h1>,
}));
vi.mock("@/pages/DocumentsPage", () => ({
  default: () => <h1>Documents page</h1>,
}));
vi.mock("@/pages/CopilotPage", () => ({
  default: () => <h1>Copilot page</h1>,
}));
vi.mock("@/pages/EmailDraftsPage", () => ({
  default: () => <h1>Email Drafts page</h1>,
}));
vi.mock("@/pages/PrivacyPage", () => ({
  default: () => <h1>Privacy page</h1>,
}));
vi.mock("@/pages/VerificationPage", () => ({
  default: () => <h1>Verification page</h1>,
}));

vi.mock("@/pages/SettingsPage", () => ({ default: () => <h1>Settings page</h1> }));
vi.mock("@/pages/SearchPage", () => ({ default: () => <h1>Search page</h1> }));
vi.mock("@/pages/CalendarSourcesPage", () => ({ default: () => <h1>Calendar sources page</h1> }));

vi.mock("@/pages/WorkspacePage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/pages/WorkspacePage")>();
  return { ...actual, default: () => <h1>Workspace page</h1> };
});

import App from "@/App";

afterEach(cleanup);

function renderAt(pathname: string) {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <App />
    </MemoryRouter>,
  );
}

describe("application route compatibility", () => {
  it.each([
    ["/", "Dashboard page"],
    ["/source-inbox", "Workspace page"],
    ["/source-inbox/source-1", "Workspace page"],
    ["/settings", "Settings page"],
    ["/search?q=uat", "Search page"],
    ["/calendar-sources/event-1", "Workspace page"],
    ["/email-drafts/draft-1", "Email Drafts page"],
    ["/knowledge", "Workspace page"],
    ["/actions", "Workspace page"],
    ["/decisions", "Workspace page"],
    ["/integrations", "Settings page"],
    ["/gmail", "Workspace page"],
    ["/documents", "Workspace page"],
    ["/copilot", "Dashboard page"],
    ["/email-drafts", "Email Drafts page"],
    ["/privacy", "Settings page"],
    ["/knowledge/knowledge-1", "Workspace page"],
    ["/actions/action-1", "Workspace page"],
    ["/decisions/decision-1", "Workspace page"],
    ["/emails/email-1", "Workspace page"],
    ["/calendar/link-1", "Action Center page"],
    ["/documents/document-1", "Workspace page"],
    ["/knowledge?business_entity_id=entity-1", "Workspace page"],
    ["/verification", "Workspace page"],
  ])("resolves %s inside the workspace shell", (path, heading) => {
    renderAt(path);

    expect(screen.getByRole("heading", { name: heading })).toBeInTheDocument();
    expect(screen.getByTestId("app-shell")).toBeInTheDocument();
  });

  it("keeps the login page outside the workspace shell", () => {
    renderAt("/login");

    expect(screen.getByRole("heading", { name: "Login page" })).toBeInTheDocument();
    expect(screen.queryByTestId("app-shell")).not.toBeInTheDocument();
  });

  it("redirects unknown paths to the dashboard", async () => {
    renderAt("/not-a-real-route");

    expect(
      await screen.findByRole("heading", { name: "Dashboard page" }),
    ).toBeInTheDocument();
  });
});
