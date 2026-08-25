import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { documentsApi, integrationsApi, privacyApi } from "@/api";
import type { IntegrationConnection, SyncStatus } from "@/api";
import IntegrationsPage from "@/pages/IntegrationsPage";
import PrivacyPage from "@/pages/PrivacyPage";

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    integrationsApi: {
      ...actual.integrationsApi,
      list: vi.fn(),
      revoke: vi.fn(),
    },
    privacyApi: {
      ...actual.privacyApi,
      syncStatus: vi.fn(),
    },
    documentsApi: {
      ...actual.documentsApi,
      list: vi.fn(),
    },
  };
});

const CONNECTION: IntegrationConnection = {
  id: "connection-1",
  organization_id: "org-1",
  user_id: "user-1",
  provider: "GOOGLE",
  service: "GMAIL",
  account_email: "owner@example.com",
  status: "CONNECTED",
  granted_scopes: ["gmail.readonly"],
  last_sync_at: null,
  last_error: null,
  created_at: "2026-08-08T00:00:00Z",
  updated_at: "2026-08-08T00:00:00Z",
};

const SYNC_STATUS: SyncStatus = {
  connection_id: CONNECTION.id,
  service: CONNECTION.service,
  account_email: CONNECTION.account_email,
  status: CONNECTION.status,
  last_sync_at: null,
  last_error: null,
};

describe("Google access revoke confirmation", () => {
  beforeEach(() => {
    vi.mocked(integrationsApi.list).mockReset().mockResolvedValue([CONNECTION]);
    vi.mocked(integrationsApi.revoke).mockReset();
    vi.mocked(privacyApi.syncStatus)
      .mockReset()
      .mockResolvedValue([SYNC_STATUS]);
    vi.mocked(documentsApi.list).mockReset().mockResolvedValue([]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("does not revoke from Integrations when confirmation is cancelled", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(
      <MemoryRouter>
        <IntegrationsPage />
      </MemoryRouter>,
    );

    await screen.findByText("owner@example.com");
    await user.click(screen.getByRole("button", { name: "Revoke access" }));

    expect(window.confirm).toHaveBeenCalledOnce();
    expect(integrationsApi.revoke).not.toHaveBeenCalled();
  });

  it("does not revoke from Privacy when confirmation is cancelled", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<PrivacyPage />);

    await screen.findByText(/owner@example\.com/);
    await user.click(screen.getByRole("button", { name: "Revoke access" }));

    expect(window.confirm).toHaveBeenCalledOnce();
    expect(integrationsApi.revoke).not.toHaveBeenCalled();
  });
});
