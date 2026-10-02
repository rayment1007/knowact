import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import SyncPreferences from "@/components/workspace/SyncPreferences";
import { clearApiCache } from "@/api/cache";

afterEach(() => { cleanup(); clearApiCache(); vi.unstubAllGlobals(); });

it("saves the chosen window, keeps it on return, and never syncs just for saving", async () => {
  let saved = { email_days: 7, calendar_past_days: 7, calendar_future_days: 90 };
  const fetcher = vi.fn(async (path: string, options: RequestInit = {}) => {
    if (options.method === "PUT") saved = JSON.parse(options.body as string);
    return new Response(JSON.stringify(path.endsWith("sync-exclusions") ? { count: 0 } : saved), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetcher);
  const view = render(<SyncPreferences />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Save sync range" })).toBeEnabled());
  expect(screen.getByLabelText("Email: past days")).toHaveValue(7);
  fireEvent.change(screen.getByLabelText("Email: past days"), { target: { value: "30" } });
  fireEvent.click(screen.getByRole("button", { name: "Save sync range" }));
  expect(await screen.findByText("Saved. Your next sync will use this range.")).toBeInTheDocument();
  expect(saved).toEqual({ email_days: 30, calendar_past_days: 7, calendar_future_days: 90 });
  view.unmount(); render(<SyncPreferences />);
  await waitFor(() => expect(screen.getByLabelText("Email: past days")).toHaveValue(30));
  expect(fetcher.mock.calls.some(([path]) => path.includes("sync-now"))).toBe(false);
});
