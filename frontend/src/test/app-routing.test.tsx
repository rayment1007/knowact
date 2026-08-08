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
    ["/source-inbox", "Source Inbox page"],
    ["/knowledge", "Knowledge Hub page"],
    ["/actions", "Action Center page"],
    ["/decisions", "Decision Memory page"],
    ["/integrations", "Integrations page"],
    ["/gmail", "Gmail Sync page"],
    ["/documents", "Documents page"],
    ["/copilot", "Copilot page"],
    ["/email-drafts", "Email Drafts page"],
    ["/privacy", "Privacy page"],
    ["/knowledge/knowledge-1", "Knowledge Hub page"],
    ["/actions/action-1", "Action Center page"],
    ["/decisions/decision-1", "Decision Memory page"],
    ["/emails/email-1", "Gmail Sync page"],
    ["/calendar/link-1", "Action Center page"],
    ["/documents/document-1", "Documents page"],
    ["/knowledge?business_entity_id=entity-1", "Knowledge Hub page"],
    ["/verification", "Verification page"],
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
