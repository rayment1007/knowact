import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/api/client";
import { clearApiCache, invalidateApiCache, peekCache } from "@/api/cache";
import { useCachedQuery } from "@/hooks/useCachedQuery";

const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("session workspace cache", () => {
  it("shares concurrent reads and keeps data across page remounts", async () => {
    const fetcher = vi.fn().mockResolvedValue(json([{ title: "Existing action" }]));
    vi.stubGlobal("fetch", fetcher);
    function Page() {
      const { data } = useCachedQuery("/actions", () => api.get<{ title: string }[]>("/actions"));
      return <p>{data?.[0].title ?? "Loading actions"}</p>;
    }
    const view = render(<Page />);
    await api.get("/actions");
    expect(await screen.findByText("Existing action")).toBeInTheDocument();
    view.unmount();
    render(<Page />);
    expect(screen.getByText("Existing action")).toBeInTheDocument();
    expect(screen.queryByText("Loading actions")).not.toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("updates affected dashboard data after a local edit and keeps unrelated data", async () => {
    let open = 1;
    const fetcher = vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === "PATCH") { open = 0; return json({ status: "DONE" }); }
      return json(url.includes("summary") ? { open_actions: open } : []);
    });
    vi.stubGlobal("fetch", fetcher);
    function Summary() {
      const { data } = useCachedQuery("/workspace/summary", () => api.get<{ open_actions: number }>("/workspace/summary"));
      return <p>Open: {data?.open_actions}</p>;
    }
    render(<Summary />);
    await Promise.all([api.get("/workspace/calendar"), api.get("/documents"), api.get("/actions")]);
    await screen.findByText("Open: 1");
    await act(async () => { await api.patch("/actions/one", { status: "DONE" }); });
    expect(await screen.findByText("Open: 0")).toBeInTheDocument();
    await api.get("/documents"); await api.get("/workspace/calendar");
    expect(fetcher.mock.calls.filter(([url]) => url === "/api/documents")).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => url === "/api/workspace/calendar")).toHaveLength(1);
    expect(fetcher.mock.calls.filter(([url]) => url === "/api/actions")).toHaveLength(2);
  });

  it("never restores a pre-edit response when an old request finishes last", async () => {
    let resolveOld!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn().mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; })).mockResolvedValue(json(["new"])));
    const old = api.get("/actions");
    invalidateApiCache(["/actions"]);
    await expect(api.get("/actions")).resolves.toEqual(["new"]);
    resolveOld(json(["old"]));
    await expect(old).resolves.toEqual(["new"]);
    expect(peekCache("/actions")).toEqual(["new"]);
  });

  it("keeps other modules after adding a note, but refreshes detached draft provenance after deleting its source", async () => {
    const fetcher = vi.fn(async (_url: string) => json([]));
    vi.stubGlobal("fetch", fetcher);
    const paths = ["/email-drafts", "/knowledge", "/gmail/messages", "/documents"];
    await Promise.all(paths.map(path => api.get(path)));
    await api.post("/source-items", { title: "New note", content: "Note" });
    await Promise.all(paths.map(path => api.get(path)));
    for (const path of paths) expect(fetcher.mock.calls.filter(call => String(call[0]) === `/api${path}`)).toHaveLength(1);
    await api.del("/source-items/deleted-source");
    await Promise.all(paths.map(path => api.get(path)));
    for (const path of paths.slice(0, 3)) expect(fetcher.mock.calls.filter(call => String(call[0]) === `/api${path}`)).toHaveLength(2);
    expect(fetcher.mock.calls.filter(call => String(call[0]) === "/api/documents")).toHaveLength(1);
  });

  it("clears private data and discards pending responses at an account boundary", async () => {
    let resolveOld!: (response: Response) => void;
    const fetcher = vi.fn().mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; })).mockResolvedValue(json(["account B"]));
    vi.stubGlobal("fetch", fetcher);
    const old = api.get("/actions");
    const rejected = expect(old).rejects.toMatchObject({ name: "AbortError" });
    clearApiCache();
    resolveOld(json(["account A"]));
    await rejected;
    expect(peekCache("/actions")).toBeUndefined();
    await expect(api.get("/actions")).resolves.toEqual(["account B"]);
  });

  it("allows one subscriber to abort while the other still receives a shared read", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise(resolve => { finish = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const controller = new AbortController();
    const first = api.get("/workspace/search?q=report", { signal: controller.signal });
    const rejected = expect(first).rejects.toMatchObject({ name: "AbortError" });
    const second = api.get("/workspace/search?q=report");
    controller.abort(); finish(json({ total: 2 }));
    await rejected;
    await expect(second).resolves.toEqual({ total: 2 });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it("does not cache failures, session probes, OAuth nonces or generated briefs", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json({}, 503)).mockImplementation(async () => json({ ok: true }));
    vi.stubGlobal("fetch", fetcher);
    await expect(api.get("/actions")).rejects.toMatchObject({ status: 503 });
    await expect(api.get("/actions")).resolves.toEqual({ ok: true });
    for (const path of ["/auth/me", "/auth/google/start", "/brief/daily"]) {
      await api.get(path); await api.get(path);
      expect(fetcher.mock.calls.filter(([url]) => url === `/api${path}`)).toHaveLength(2);
    }
  });

  it("refreshes an active view with Sync Now and reports a failed refresh", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(json({ total: 1 })).mockResolvedValueOnce(json({}, 503)).mockResolvedValue(json({ total: 2 }));
    vi.stubGlobal("fetch", fetcher);
    function Page() {
      const { data, error } = useCachedQuery("/workspace/calendar", () => api.get<{ total: number }>("/workspace/calendar"));
      return <p>{error ? "Refresh failed" : `Events: ${data?.total}`}</p>;
    }
    render(<Page />);
    await screen.findByText("Events: 1");
    act(() => invalidateApiCache(["*"]));
    await screen.findByText("Refresh failed");
    act(() => invalidateApiCache(["*"]));
    await waitFor(() => expect(screen.getByText("Events: 2")).toBeInTheDocument());
  });
});
