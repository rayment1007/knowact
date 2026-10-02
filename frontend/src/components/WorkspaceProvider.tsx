import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { invalidateApiCache, matchesPaths, subscribeCacheInvalidation } from "@/api/cache";
import { gmailApi, integrationsApi, type IntegrationConnection } from "@/api";
import { workspaceApi, type WorkspaceSummary } from "@/api/workspace";
import { getErrorMessage } from "@/api/errors";

interface WorkspaceState {
  connections: IntegrationConnection[]; summary: WorkspaceSummary | null;
  syncing: boolean; loading: boolean; error: string | null;
  failures: Record<string, string>;
  sync: () => Promise<void>; refresh: () => Promise<void>;
}
const Context = createContext<WorkspaceState | null>(null);
export const useWorkspace = () => {
  const context = useContext(Context);
  if (!context) throw new Error("WorkspaceProvider is required");
  return context;
};

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [connections, setConnections] = useState<IntegrationConnection[]>([]);
  const [summary, setSummary] = useState<WorkspaceSummary | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [failures, setFailures] = useState<Record<string, string>>({});
  const inFlight = useRef(false);
  const started = useRef(false);
  const refreshGeneration = useRef(0);

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    const results = await Promise.allSettled([integrationsApi.list(), workspaceApi.summary()]);
    if (generation !== refreshGeneration.current) return;
    if (results[0].status === "fulfilled") setConnections(results[0].value);
    if (results[1].status === "fulfilled") setSummary(results[1].value);
    setError(results.some(result => result.status === "rejected") ? "Could not refresh workspace status. Showing previously loaded data where available. Retry with Sync Now." : null);
    setLoading(false);
  }, []);

  const sync = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setSyncing(true);
    setError(null);
    setFailures({});
    let connectionError = false;
    try {
      invalidateApiCache(["/integrations"], false);
      const current = await integrationsApi.list();
      setConnections(current);
      const active = current.filter(connection => connection.status === "CONNECTED");
      await Promise.allSettled(active.map(async connection => {
        try {
          if (connection.service === "GMAIL") await gmailApi.syncNow(connection.id);
          else if (connection.service === "GOOGLE_CALENDAR") await workspaceApi.syncCalendar(connection.id);
        } catch (error) {
          setFailures(previous => ({ ...previous, [connection.id]: getErrorMessage(error, "Sync failed. Retry or check Settings.") }));
        }
      }));
    } catch {
      connectionError = true;
    } finally {
      // A user-requested sync refreshes all captured data, including edits from
      // another tab/device. Ordinary navigation never invalidates the cache.
      invalidateApiCache(["*"]);
      await refresh();
      if (connectionError) setError("Could not read Google connections for this sync. Retry with Sync Now.");
      inFlight.current = false;
      setSyncing(false);
      setLoading(false);
    }
  }, [refresh]);

  useEffect(() => {
    if (!started.current) {
      started.current = true;
      void sync();
    }
  }, [sync]);
  useEffect(() => subscribeCacheInvalidation(paths => {
    if (!inFlight.current && ["/integrations", "/workspace/summary"].some(path => matchesPaths(path, paths))) void refresh();
  }), [refresh]);

  return <Context.Provider value={{ connections, summary, syncing, loading, error, failures, sync, refresh }}>{children}</Context.Provider>;
}
