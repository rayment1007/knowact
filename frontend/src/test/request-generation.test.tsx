import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { sourceItemsApi } from "@/api";
import type { SourceItem } from "@/api";
import SourceInboxPage from "@/pages/SourceInboxPage";

vi.mock("@/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api")>();
  return {
    ...actual,
    sourceItemsApi: {
      ...actual.sourceItemsApi,
      list: vi.fn(),
    },
  };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

function sourceItem(id: string, title: string): SourceItem {
  return {
    id,
    organization_id: "org-1",
    created_by: "user-1",
    source_type: "MANUAL",
    title,
    content: `${title} content`,
    status: "NEW",
    received_at: "2026-08-08T00:00:00Z",
    created_at: "2026-08-08T00:00:00Z",
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("latest request wins", () => {
  it("does not let a slow old filter response replace a faster new response", async () => {
    const user = userEvent.setup();
    const slowRequest = deferred<SourceItem[]>();
    const staleItem = sourceItem("source-a", "Slow stale item");
    const currentItem = sourceItem("source-b", "Fast current item");

    vi.mocked(sourceItemsApi.list).mockImplementation((filters) =>
      filters?.status === "NEW"
        ? Promise.resolve([currentItem])
        : slowRequest.promise,
    );

    render(<SourceInboxPage />);
    await user.selectOptions(screen.getByLabelText("Status"), "NEW");

    expect(await screen.findByText("Fast current item")).toBeInTheDocument();

    await act(async () => {
      slowRequest.resolve([staleItem]);
      await slowRequest.promise;
    });

    expect(screen.getByText("Fast current item")).toBeInTheDocument();
    expect(screen.queryByText("Slow stale item")).not.toBeInTheDocument();
  });
});
