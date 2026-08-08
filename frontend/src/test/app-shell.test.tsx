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
  it("shows exactly six purpose links in the mobile drawer", async () => {
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
      "Sources",
      "Knowledge",
      "Actions",
      "Copilot",
      "Settings",
    ]);
  });

  it("takes a purpose link to its first page and reveals context tabs", async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole("link", { name: "Sources" }));

    expect(screen.getByLabelText("current path")).toHaveTextContent(
      "/source-inbox",
    );
    const tabs = screen.getByRole("navigation", { name: "Sources pages" });
    expect(within(tabs).getAllByRole("link").map((link) => link.textContent)).toEqual([
      "Inbox",
      "Gmail",
      "Files",
    ]);
  });
});
