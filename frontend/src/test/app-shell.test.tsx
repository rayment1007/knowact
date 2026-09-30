import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MemoryRouter,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";

vi.mock("@/auth", () => ({
  useAuth: () => ({
    user: {
      email: "owner@example.com",
      full_name: "Workspace Owner",
    },
    organization: { name: "KnowAct Workspace" },
    logout: vi.fn(),
  }),
}));

vi.mock("@/api/integrations", () => ({ integrationsApi: { list: vi.fn().mockResolvedValue([]) } }));
vi.mock("@/api/workspace", () => ({ workspaceApi: { summary: vi.fn().mockResolvedValue({ documents: 0, documents_pending: 0, documents_failed: 0, notes: 0, knowledge: 0, open_actions: 0 }) } }));

import AppShell from "@/layouts/AppShell";

afterEach(cleanup);

function LocationProbe() {
  const location = useLocation();
  return <output aria-label="current path">{location.pathname}</output>;
}

function ShellRouteContent() {
  return (
    <>
      <LocationProbe />
      <Outlet />
    </>
  );
}

function renderShell(pathname = "/") {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="*" element={<ShellRouteContent />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("consolidated workspace shell", () => {
  it("places account and logout in the sidebar with compact sync in the header", () => {
    renderShell();
    const sidebar = screen.getByRole("complementary", { name: "Main sidebar" });
    expect(within(sidebar).getByText("Workspace Owner")).toBeInTheDocument();
    expect(within(sidebar).getByRole("button", { name: "Log out" })).toBeInTheDocument();
    expect(within(screen.getByRole("banner")).getByRole("button", { name: "Sync status" })).toBeInTheDocument();
    expect(within(screen.getByRole("banner")).queryByText("Workspace Owner")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Google sync details")).not.toBeInTheDocument();
  });
  it("shows exactly two purpose links in the mobile drawer", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("button", { name: "Open navigation" }));
    const dialog = screen.getByRole("dialog", {
      name: "Workspace navigation",
    });
    const labels = within(dialog)
      .getAllByRole("link")
      .map((link) => link.textContent);

    expect(labels).toEqual([
      "Dashboard",
      "Workspace",
    ]);
  });

  it("opens Workspace without the old source-type navigation tabs", async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole("link", { name: "Workspace" }));
    expect(screen.getByLabelText("current path")).toHaveTextContent("/workspace/sources");
    expect(screen.queryByRole("navigation", { name: "Sources pages" })).not.toBeInTheDocument();
  });
});
