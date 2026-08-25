import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, authApi } from "@/api";
import AuthProvider from "@/auth/AuthProvider";
import { useAuth } from "@/auth";

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    authApi: {
      ...actual.authApi,
      getCurrentUser: vi.fn(),
      login: vi.fn(),
      logout: vi.fn(),
    },
    setUnauthorizedHandler: vi.fn(),
  };
});

const CURRENT_USER = {
  user: {
    id: "user-1",
    organization_id: "org-1",
    email: "owner@example.com",
    full_name: "Workspace Owner",
    role: "ADMIN" as const,
    created_at: "2026-08-08T00:00:00Z",
  },
  organization: {
    id: "org-1",
    name: "KnowAct Workspace",
    created_at: "2026-08-08T00:00:00Z",
  },
};

function SessionProbe() {
  const { status, retrySession, logout } = useAuth();
  return (
    <div>
      <output aria-label="session status">{status}</output>
      <button type="button" onClick={retrySession}>
        Retry session
      </button>
      <button
        type="button"
        onClick={() => {
          void logout().catch(() => undefined);
        }}
      >
        Log out
      </button>
    </div>
  );
}

function renderProvider() {
  return render(
    <AuthProvider>
      <SessionProbe />
    </AuthProvider>,
  );
}

describe("AuthProvider session hardening", () => {
  afterEach(cleanup);

  beforeEach(() => {
    vi.mocked(authApi.getCurrentUser).mockReset();
    vi.mocked(authApi.login).mockReset();
    vi.mocked(authApi.logout).mockReset();
  });

  it("keeps a temporary restore failure distinct from unauthenticated", async () => {
    vi.mocked(authApi.getCurrentUser).mockRejectedValueOnce(
      new TypeError("network unavailable"),
    );

    renderProvider();

    expect(await screen.findByLabelText("session status")).toHaveTextContent(
      "error",
    );
  });

  it("can retry session restoration after a temporary failure", async () => {
    const user = userEvent.setup();
    vi.mocked(authApi.getCurrentUser)
      .mockRejectedValueOnce(new ApiError(503, "Unavailable"))
      .mockResolvedValueOnce(CURRENT_USER);

    renderProvider();
    expect(await screen.findByLabelText("session status")).toHaveTextContent(
      "error",
    );

    await user.click(screen.getByRole("button", { name: "Retry session" }));

    expect(await screen.findByLabelText("session status")).toHaveTextContent(
      "authenticated",
    );
  });

  it("does not claim logout succeeded when the server is unreachable", async () => {
    const user = userEvent.setup();
    vi.mocked(authApi.getCurrentUser).mockResolvedValueOnce(CURRENT_USER);
    vi.mocked(authApi.logout).mockRejectedValueOnce(
      new TypeError("network unavailable"),
    );

    renderProvider();
    expect(await screen.findByLabelText("session status")).toHaveTextContent(
      "authenticated",
    );

    await user.click(screen.getByRole("button", { name: "Log out" }));
    await act(async () => undefined);

    expect(screen.getByLabelText("session status")).toHaveTextContent(
      "authenticated",
    );
  });

  it("treats a logout 401 as already logged out", async () => {
    const user = userEvent.setup();
    vi.mocked(authApi.getCurrentUser).mockResolvedValueOnce(CURRENT_USER);
    vi.mocked(authApi.logout).mockRejectedValueOnce(
      new ApiError(401, "Expired"),
    );

    renderProvider();
    expect(await screen.findByLabelText("session status")).toHaveTextContent(
      "authenticated",
    );

    await user.click(screen.getByRole("button", { name: "Log out" }));

    expect(await screen.findByLabelText("session status")).toHaveTextContent(
      "unauthenticated",
    );
  });
});
