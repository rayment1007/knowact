// Private, in-memory session cache. Never persist source content in browser storage.
type Entry = { data?: unknown; pending?: Promise<unknown>; read: () => Promise<unknown> };
const entries = new Map<string, Entry>();
const listeners = new Set<(paths: string[]) => void>();
let sessionGeneration = 0;
const MAX_ENTRIES = 120;

export const cacheSession = () => sessionGeneration;
export const cacheKey = (path: string) => {
  const [base, query] = path.split("?");
  const params = new URLSearchParams(query);
  params.sort();
  return base + (params.size ? `?${params}` : "");
};
export const canCache = (path: string) => /^\/(workspace|source-items|knowledge|actions|documents|gmail|email-drafts|business-entities|integrations|privacy|calendar)(\/|\?|$)/.test(path)
  && !/\/(callback|connect|authorize|calendars)(\?|$)/.test(path);

export function peekCache<T>(path: string): T | undefined {
  return entries.get(cacheKey(path))?.data as T | undefined;
}

export function subscribeCacheInvalidation(listener: (paths: string[]) => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function clearApiCache() {
  sessionGeneration++;
  entries.clear();
}

// A caller's cancellation must not cancel a shared request used by another page.
function forSubscriber<T>(request: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return request;
  if (signal.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", abort, { once: true });
    request.then(value => { if (!signal.aborted) resolve(value); }, reject)
      .finally(() => signal.removeEventListener("abort", abort));
  });
}

export function cachedGet<T>(path: string, read: () => Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (signal?.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
  const key = cacheKey(path);
  let entry = entries.get(key);
  if (entry?.data !== undefined) return forSubscriber(Promise.resolve(entry.data as T), signal);
  if (entry?.pending) return forSubscriber(entry.pending as Promise<T>, signal);
  entry = { read };
  entries.set(key, entry);
  if (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value!);
  const current = entry;
  const session = sessionGeneration;
  const pending = read().then(data => {
    if (session !== sessionGeneration) throw new DOMException("Session changed", "AbortError");
    if (entries.get(key) !== current) {
      // An edit overtook this read. Never publish the pre-edit response.
      return cachedGet(path, read);
    }
    current.data = data;
    current.pending = undefined;
    return data;
  }, error => {
    if (entries.get(key) === current) entries.delete(key);
    throw error;
  });
  current.pending = pending;
  return forSubscriber(pending, signal);
}

export function matchesPaths(path: string, prefixes: string[]) {
  const key = cacheKey(path);
  return prefixes.some(value => {
    const prefix = cacheKey(value);
    return prefix === "*" || key === prefix || key.startsWith(prefix + "/") || key.startsWith(prefix + "?");
  });
}

export function invalidateApiCache(prefixes: string[], revalidate = true) {
  const reload: [string, Entry][] = [];
  for (const [key, entry] of entries) {
    if (matchesPaths(key, prefixes)) {
      entries.delete(key);
      reload.push([key, entry]);
    }
  }
  // Refresh only previously visited, affected queries. No provider/AI calls here:
  // these loaders are authenticated, read-only GETs to captured workspace data.
  if (revalidate) for (const [key, entry] of reload) {
    // Retain the dashboard and main lists. Older searches/detail pages refresh
    // on demand, avoiding a burst of requests for every previously typed query.
    const retained = /^\/(actions|knowledge|source-items|documents|integrations|email-drafts)$/.test(key)
      || ["/workspace/summary", "/workspace/calendar", cacheKey("/workspace/search?q=&kind=all&offset=0"), cacheKey("/workspace/activity?offset=0&limit=5")].includes(key);
    if (retained) void cachedGet(key, entry.read).catch(() => {});
  }
  for (const listener of listeners) listener(prefixes);
}

export function mutationDependencies(path: string, method = "POST"): string[] {
  const root = path.split("/")[1];
  const workspace = ["/workspace/summary", "/workspace/search", "/workspace/activity", "/workspace/items", "/workspace/counts", "/workspace/source", "/workspace/proposals", "/workspace/knowledge"];
  const dependencies: Record<string, string[]> = {
    workspace: ["/workspace/sync-preferences", "/workspace/sync-exclusions", "/workspace/proposals", "/workspace/knowledge", "/workspace/actions", "/knowledge", "/actions"],
    actions: ["/actions", "/knowledge", "/workspace/actions", ...(path.endsWith("/add-to-calendar") ? ["/workspace/calendar", "/calendar"] : method === "DELETE" ? ["/calendar"] : [])],
    knowledge: ["/knowledge", "/source-items", ...(method === "DELETE" ? ["/actions"] : [])],
    "source-items": ["/source-items", ...(method === "DELETE" ? ["/knowledge", "/gmail", "/email-drafts", "/workspace/actions"] : path.endsWith("/extract") ? ["/knowledge"] : [])],
    documents: ["/documents", "/workspace/files", ...(method === "DELETE" ? ["/workspace/actions"] : [])],
    gmail: ["/gmail", "/source-items", "/actions", "/workspace/actions", "/workspace/suggestions", "/knowledge", "/documents", "/integrations", "/privacy"],
    calendar: ["/calendar", "/workspace/calendar", "/workspace/actions", "/integrations", "/privacy"],
    integrations: ["/integrations", "/privacy"],
    "email-drafts": ["/email-drafts", "/gmail", "/source-items"],
    privacy: ["/privacy", "/integrations", "/gmail", "/documents", "/source-items", "/knowledge", "/email-drafts", "/workspace/files", "/workspace/suggestions", "/workspace/actions"],
    copilot: ["/source-items", "/knowledge", "/actions", "/email-drafts"],
    "business-entities": ["/business-entities", "/actions", "/knowledge"],
  };
  if (root === "integrations") return dependencies[root];
  if (root === "copilot" && !path.endsWith("/confirm")) return [];
  return dependencies[root] ? [...dependencies[root], ...workspace] : [];
}
