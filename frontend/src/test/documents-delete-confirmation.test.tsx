import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { documentsApi } from "@/api";
import type { DocumentAsset } from "@/api";
import DocumentsPage from "@/pages/DocumentsPage";

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    documentsApi: {
      ...actual.documentsApi,
      list: vi.fn(),
      get: vi.fn(),
      remove: vi.fn(),
    },
  };
});

const DOCUMENT: DocumentAsset = {
  id: "document-1",
  organization_id: "org-1",
  uploaded_by: "user-1",
  filename: "proposal.pdf",
  mime_type: "application/pdf",
  checksum: "checksum",
  processing_status: "INDEXED",
  failure_reason: null,
  sensitivity: "INTERNAL",
  source_deleted: false,
  created_at: "2026-08-08T00:00:00Z",
};

function renderDocuments(path = "/documents") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="documents/:documentId" element={<DocumentsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("document deletion confirmation", () => {
  beforeEach(() => {
    vi.mocked(documentsApi.list).mockReset().mockResolvedValue([DOCUMENT]);
    vi.mocked(documentsApi.get).mockReset().mockResolvedValue(DOCUMENT);
    vi.mocked(documentsApi.remove).mockReset().mockResolvedValue(undefined);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("does not delete from the list when confirmation is cancelled", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderDocuments();

    await screen.findByText("proposal.pdf");
    await user.click(screen.getByRole("button", { name: "Delete" }));

    expect(window.confirm).toHaveBeenCalledOnce();
    expect(documentsApi.remove).not.toHaveBeenCalled();
  });

  it("requires confirmation before deleting from the opened detail", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderDocuments("/documents/document-1");

    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: "Delete" })).toHaveLength(2);
    });
    const deleteButtons = screen.getAllByRole("button", { name: "Delete" });
    await user.click(deleteButtons[0]);

    expect(window.confirm).toHaveBeenCalledOnce();
    await waitFor(() => {
      expect(documentsApi.remove).toHaveBeenCalledOnce();
      expect(documentsApi.remove).toHaveBeenCalledWith("document-1");
    });
  });
});
